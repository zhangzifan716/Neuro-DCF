import os
import argparse
import gym
import numpy as np
import ray
from collections import defaultdict
from ray import tune
from ray.tune.registry import register_env
from ray.rllib.env.multi_agent_env import MultiAgentEnv
from ns3gym import ns3env
from link_graph import read_graph


class Ns3MultiAgentEnv(MultiAgentEnv):

    def __init__(self, env_config):
        self.worker_index = env_config.worker_index
        self.topology = env_config.get("topology", "fim")
        _, self.n_agents = read_graph(self.topology)
        # self.n_agents = len(flows)
        port = 0
        stepTime = env_config.get("stepTime", 0.005)
        seed = env_config.get("seed", 0)
        cwd = env_config.get("cwd", None)
        traffic = env_config.get("traffic", "cbr")
        intensity = env_config.get("intensity", 1)
        delayRewardWeight = env_config.get("delayRewardWeight", 0.0)
        randomIntensity = env_config.get("randomIntensity", False)

        self.debug = env_config.get("debug", False)
        simTime_default = 1 if self.debug else 20
        simTime = env_config.get("simTime", simTime_default)

        randomFlow = env_config.get("randomFlow", False)

        self.reward = env_config.get("reward", "shared")
        assert self.reward in ["indiv", "shared"], 'self.reward must be either "indiv" or "shared"'

        simArgs = {"--simTime": simTime,
                   "--stepTime": stepTime,
                   "--topology": self.topology,
                   "--traffic": traffic,
                   "--intensity": intensity,
                   "--algorithm": "rl",
                   "--randomFlow": randomFlow,
                   "--randomIntensity": randomIntensity,
                   "--delayRewardWeight": delayRewardWeight,
                   "--RC_mode": True,
                   "--debug": self.debug}

        print("worker {} start".format(self.worker_index)) if self.debug else None

        self._env = ns3env.Ns3Env(port=port, stepTime=stepTime, startSim=True,
                                  simSeed=seed, simArgs=simArgs, debug=self.debug, cwd=cwd)

        self.multi_obs_space = self._env.observation_space
        self.multi_act_space = self._env.action_space

        self.observation_space = gym.spaces.Box(
            low=self.multi_obs_space.low[0], high=self.multi_obs_space.high[0], dtype=self.multi_obs_space.dtype)
        self.action_space = self.multi_act_space.spaces[0]

    def obs_to_dict(self, obs):
        multi_obs = np.array(obs).reshape(self.multi_obs_space.shape)
        return {i: multi_obs[i] for i in range(self.n_agents)}

    def info_to_rew(self, info):
        reward = {}
        for kv in info.split():
            k, v = kv.split('=')
            if k.startswith("reward"):  # "reward_*" keys
                index = int(k.split('_')[1])
                reward[index] = float(v)
        return reward

    def parse_info(self, info, obs, state):
        common_info = {"state": np.array(state)}  # for centralized critic
        for kv in info.split():
            k, v = kv.split('=')
            if k.startswith("reward"):  # ignore "reward_*" keys
                continue
            common_info[k] = float(v)

        ret = {}
        for i in range(self.n_agents):
            o = obs[i]
            ret[i] = {
                "tx_rate": o[0],
                "tx_rate_ewma": o[1],
                "latency": o[2],
                "loss_rate": o[3],
                "common": common_info,
            }

        # log the individual rewards if exist
        for kv in info.split():
            k, v = kv.split('=')
            if k.startswith("reward"):
                index = int(k.split('_')[1])
                ret[index]["reward"] = float(v)

        return ret

    def reset(self):
        # print("worker {} reset".format(self.worker_index)) if self.debug else None
        print("worker {} reset".format(self.worker_index))
        obs = self._env.reset()
        return self.obs_to_dict(obs)

    def step(self, action_dict):
        action = [action_dict[i] for i in range(self.n_agents)]
        o, r, d, i = self._env.step(action)
        obs = self.obs_to_dict(o)
        if self.reward == "indiv":
            rew = self.info_to_rew(i) # individual rewards
        elif self.reward == "shared":
            rew = {i: r for i in range(self.n_agents)}  # shared rewards
        else:
            raise AssertionError
        done = {"__all__": d}
        info = self.parse_info(i, obs, state=o)
        return obs, rew, done, info

    def close(self):
        # print("worker {} close".format(self.worker_index)) if self.debug else None
        print("worker {} close".format(self.worker_index))
        self._env.close()


def on_episode_start(info):
    print("on_episode_start called in worker {}".format(info["env"].envs[0].worker_index))
    print(info["policy"])
    episode = info["episode"]
    episode.user_data = defaultdict(list)  # clear user data


def on_episode_step(info):
    episode = info["episode"]
    n_agents = info["env"].envs[0].n_agents

    # process per_agent info
    for i in range(n_agents):
        for k, v in episode.last_info_for(i).items():
            if k != "common":
                episode.user_data[k+"_"+str(i)].append(v)
                
    # process common info
    for k, v in episode.last_info_for(0).get("common", {}).items():
        if k != "state":
            episode.user_data[k].append(v)


def on_episode_end(info):
    episode = info["episode"]
    for k, v in episode.user_data.items():
        if "loss_rate" in k:
            # MYTODO np.mean is not technically correct at this point
            agg = np.mean(v)
        else:
            agg = np.sum(v)
        episode.custom_metrics[k] = agg


register_env("ns3_multiagent_env", lambda env_config: Ns3MultiAgentEnv(env_config))

if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stop", help="number of timesteps, default 3e8", type=int, default=3e8)
    parser.add_argument(
        "--debug", help="debug indicator, default false", type=bool, default=False)

    args = parser.parse_args()

    ray.init(log_to_driver=args.debug)
    cwd = os.path.dirname(os.path.abspath(__file__))
    topology = "fim"

    NUM_GPUS = 4
    num_workers = 4

    if args.debug:
        params_list = [0]
    else:
        # params_list = [0]
        params_list = [5e-4, 5e-5, 5e-6, 5e-7]  # for parameter testing
    num_samples = 1
    num_workers_total = num_workers * len(params_list) * num_samples  # <= 32 is recommended
    num_gpus_per_worker = NUM_GPUS / num_workers_total

    tune.run(
        "PPO",
        stop={"timesteps_total": args.stop},
        config={
            "env": "ns3_multiagent_env",
            "batch_mode": "complete_episodes",
            "log_level": "DEBUG" if args.debug else "WARN",
            "num_workers": 0 if args.debug else num_workers,
            "num_gpus_per_worker": num_gpus_per_worker,
            "lr": 5e-5 if args.debug else tune.grid_search(params_list),
            "env_config": {
                "cwd": cwd,
                "debug": args.debug,
                "reward": "shared",
                "topology": topology,
                "traffic": "cbr",
            },
            "callbacks": {
                "on_episode_start": on_episode_start,
                "on_episode_step": on_episode_step,
                "on_episode_end": on_episode_end
            },
        },
        num_samples=1 if args.debug else num_samples,
    )
