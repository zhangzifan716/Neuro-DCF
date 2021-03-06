#include "ns3/pointer.h"
#include "ns3/log.h"
#include "ns3/string.h"
#include "ns3/socket.h"
#include "ns3/simulator.h"
#include "random-generator.h"

namespace ns3 {

NS_LOG_COMPONENT_DEFINE ("RandomGenerator");

NS_OBJECT_ENSURE_REGISTERED (RandomGenerator);

TypeId
RandomGenerator::GetTypeId (void)
{
  static TypeId tid = TypeId ("ns3::RandomGenerator")
    .SetParent<Application> ()
    .SetGroupName ("Applications")
    .AddConstructor<RandomGenerator> ()
    .AddAttribute ("Delay1", "The delay1 between two packets (s)",
                    StringValue ("ns3::ConstantRandomVariable[Constant=1]"),
                    MakePointerAccessor (&RandomGenerator::m_delay1),
                    MakePointerChecker<RandomVariableStream> ())
    .AddAttribute ("Delay2", "The delay2 between two packets (s)",
                    StringValue ("ns3::ConstantRandomVariable[Constant=1]"),
                    MakePointerAccessor (&RandomGenerator::m_delay2),
                    MakePointerChecker<RandomVariableStream> ())
    .AddAttribute ("ModDelay", "The transition time of the modulation process",
                    StringValue ("ns3::ConstantRandomVariable[Constant=1]"),
                    MakePointerAccessor (&RandomGenerator::m_modDelay),
                    MakePointerChecker<RandomVariableStream> ())
    .AddAttribute ("Size", "The size of each packet (bytes)",
                    StringValue ("ns3::ConstantRandomVariable[Constant=2000]"),
                    MakePointerAccessor (&RandomGenerator::m_size),
                    MakePointerChecker<RandomVariableStream> ());
  return tid;
}

RandomGenerator::RandomGenerator ()
{
  m_next = EventId ();
}

void
RandomGenerator::SetRemote (std::string socketType, Address remote)
{
  m_socketType = TypeId::LookupByName (socketType);
  m_peerAddress = remote;
}

void
RandomGenerator::StartApplication (void)
{
  m_mode = 1;
  m_lastMod = Simulator::Now ();
  m_sampledModDelay = Seconds (0.0);
  m_socket = Socket::CreateSocket (GetNode (), m_socketType);
  m_socket->Bind ();
  m_socket->ShutdownRecv ();
  m_socket->Connect (m_peerAddress);
  DoGenerate ();
}

void
RandomGenerator::StopApplication (void)
{
  Simulator::Cancel (m_next);
}

void
RandomGenerator::DoGenerate (void)
{
  if ((Simulator::Now () - m_lastMod) >= m_sampledModDelay)
    {
      /* Switch mode */
      m_mode = 3 - m_mode;  // 1->2, 2->1
      m_sampledModDelay = Seconds (m_modDelay->GetValue ());
      m_lastMod = Simulator::Now ();
    }

  /* Packet delay sampling */
  Time delay;
  NS_ASSERT (m_mode == 1 || m_mode == 2);
  if (m_mode == 1)
    delay = Seconds (m_delay1->GetValue ());
  else if (m_mode == 2)
    delay = Seconds (m_delay2->GetValue ());
  
  m_next = Simulator::Schedule (delay, &RandomGenerator::DoGenerate, this);
  uint32_t size;
  const uint32_t size_lb = 20;
  while (1)
    {
      size = m_size->GetInteger ();
      if (size >= size_lb) break;
    }
  Ptr<Packet> p = Create<Packet> (size);
  if ((m_socket->Send (p)) >= 0)
    {
      NS_LOG_INFO ("TraceDelay TX " << p->GetSize () << " bytes Time: "
                                    << (Simulator::Now ()).GetSeconds ());
    }
  else
    {
      NS_LOG_INFO ("Error while sending " << p->GetSize () << " bytes");
    }
  m_socket->Send (p);
}

} // namespace ns3