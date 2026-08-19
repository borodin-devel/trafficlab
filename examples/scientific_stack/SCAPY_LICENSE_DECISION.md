# [SECTION-1-14d1693c] Scapy probe license and adoption decision

## [SECTION-2-d6d6d2a8] Scope of this record

This record applies only to the Trafficlab Scapy 2.7.0 PCAPNG probe. The probe
is development-only. It copies no Scapy source code, makes no production import,
and does not change the production PCAPNG codec or canonical artifact bytes.

This record does not provide legal advice and does not decide whether a future
runtime dependency would be compatible with any license Trafficlab may adopt.

## [SECTION-3-604e7aef] Current license gate

The locked Scapy 2.7.0 package identifies its license as GPL-2.0-only. The
repository currently has no project license file and this implementation task
has no authority to choose one or resolve compatibility. Production adoption is
therefore blocked pending a separate compatibility decision by an authorized
human, regardless of the technical probe result.

## [SECTION-4-0846a0ec] Technical decision

The machine-readable result is
[`scapy_cases.json`](scapy_cases.json). A malformed-input, deadline, trace,
typing, time, or peak-RSS gate failure independently rejects technical adoption
and retains `trafficlab.pcapng`. A technical pass does not override the separate
license gate.

## [SECTION-5-4e37b215] Production status

Scapy remains in the development dependency group. The installed `trafficlab`
package does not import Scapy, production capture validation continues to use
the existing codec, and no production artifact bytes changed in this probe.
