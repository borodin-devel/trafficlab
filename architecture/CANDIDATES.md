# Traffic Model and Similarity Method Candidates

## Status and purpose

This document is a non-normative research catalog. It records classical traffic
model families and similarity methods that may be evaluated against
Trafficlab's implemented algorithms; it does not define implemented behavior,
commit future work, or track task status. The authoritative runtime families
are Poisson empirical, Markov Renewal, two-state MMPP, piecewise-constant NHPP,
and exponential ACD as defined in [Traffic Models](traffic_models/README.md).
The authoritative similarity methods remain frame-size KS, IAT KS,
autocorrelation, and multiscale rate as defined in
[Similarity Methods](similarity_methods/README.md).

The priority labels express research value for the canonical data and artifact
contract available in this repository. `Required` therefore means
"required in the next comparative model study before making broader fidelity
claims," not "required for the present MVP to operate." A candidate becomes
part of the normative architecture only when its implementation, focused tests,
mathematical document, registry entry, deterministic fixtures, and direct
scientific validation are added together.

## Compatibility boundary and research rationale

The canonical trace contains a timestamp, outbound/inbound direction relative
to the target MAC, and Ethernet frame length for each packet. User-facing
visualization may label those directions uplink/downlink, but that display
translation does not change the canonical data. Generated frames have fixed
Ethernet addresses and a zero-filled body, so they do not contain usable IP
addresses, ports, transport protocol, TCP state, flow identity, or application
semantics.

Candidate priority is based on whether a family can represent IAT dependence,
packet-volume dynamics, and multiscale burst structure using the current
`(timestamp, direction, frame_length)` representation, its identifiability from
finite captures, its fitting and generation cost, and its fit with a
deterministic single-process classical-model prototype. Dated experiment
results and performance evidence belong in research reports outside
`architecture/`.

Every implemented candidate must retain the existing closed observation-window
semantics, deterministic local RNG, reliability guards, complete
serialization, and common-family evaluation protocol. Direction and frame
length should normally be modeled jointly and, where a latent state exists,
conditionally on that state. High-dimensional matrices, kernels, and emission
parameters should be fitted by likelihood, EM, or moments; the genetic
algorithm should search only a small bounded set of structural
hyperparameters.

## Priority definitions

| Priority | Meaning |
|---|---|
| **Required** | Highest-value additions needed for the next credible model and similarity comparison on the existing dumps. They fit the current canonical trace and have a bounded implementation path. |
| **Nice to have** | Strong scientific additions after the required set. They add materially different dependence but need more fitting care or evidence. |
| **Optional** | Valid classical experiments with narrower applicability, substantial overlap, or an extra packet-disaggregation contract. |
| **Expensive (special requirements)** | Scientifically valid families that need costly inference, substantially more metadata, live protocol stacks, or an architectural scope change. |
| **Not recommended** | Poor matches for the current event-level artifact and metrics, superseded by better candidates, or explicitly excluded by approved scope. |

## Traffic models — Required

### Packet-level Hidden Markov Model

A small hidden state sequence controls a joint emission distribution for IAT,
direction, and frame length. It can represent idle, interactive, burst, and
sustained-transfer regimes without needing flow headers. Fit two to four states
with bounded Baum--Welch/EM and use held-out likelihood plus the common
similarity components for model selection. The expected fitting cost is
`O(N K^2)` per iteration.

This is the best general first addition because it models timing and marks
together, whereas the current MMPP uses regime-independent empirical marks.
The main risks are state-count overfitting, local optima, and poorly identified
emission families.

Primary evidence: Dainotti et al.,
[“Internet traffic modeling by means of Hidden Markov Models”](https://doi.org/10.1016/j.comnet.2008.05.004),
*Computer Networks*, 2008.

### Markov packet-train model

Segment traffic into short-gap packet trains separated by longer gaps, then
model train length, within-train IAT, frame marks, direction sequence, and
successive train transitions. This is a direct, near-linear packet-event model
and the lowest-cost candidate aimed specifically at multiscale burst structure.

The train-gap threshold must be frozen from a declared reference-only rule and
reported in the fitted artifact. Results are threshold-dependent, and the
original evidence predates modern Internet workloads, so validation on every
capture class is essential.

Primary evidence: Jain and Routhier,
[“Packet Trains—Measurements and a New Model for Computer Network Traffic”](https://doi.org/10.1109/JSAC.1986.1146410),
*IEEE Journal on Selected Areas in Communications*, 1986.

### Autoregressive Conditional Duration

An ACD model makes the conditional expected IAT depend recursively on previous
durations. It supplies a compact likelihood-based model for duration clustering
without a latent continuous-time Markov chain. Simple orders fit and generate
in linear time.

The innovation law, initialization, and stationarity constraints must be
explicit. Direction and frame length require a joint empirical or
state/lag-conditioned mark law.

Primary evidence: Engle and Russell,
[“Autoregressive Conditional Duration: A New Model for Irregularly Spaced Transaction Data”](https://doi.org/10.2307/2999632),
*Econometrica*, 1998.

### Non-homogeneous Poisson process

An NHPP uses a deterministic time-varying intensity, represented by a small
piecewise-constant, spline, or otherwise bounded basis. It can reproduce
repeatable startup, active, and cooldown phases and control packet volume over
the complete observation window. Fit by point-process likelihood or frozen-bin
counts and generate by integrated-intensity inversion or thinning.

It remains conditionally independent and cannot explain stochastic burst
clustering by itself. A flexible intensity fitted to one trace can overfit, so
basis size and knots must be bounded and validated on held-out windows.

Primary evidence: Lewis and Shedler,
[“Simulation of Nonhomogeneous Poisson Processes by Thinning”](https://doi.org/10.1002/nav.3800260304),
*Naval Research Logistics Quarterly*, 1979.

## Traffic models — Nice to have

### Parametric and empirical renewal models

Renewal timing provides a necessary intermediate baseline between homogeneous
Poisson and dependent models. Candidate IAT laws include Gamma/Erlang, Weibull,
lognormal, Pareto, hyperexponential, and phase-type distributions, plus a
nonparametric empirical IAT distribution. These are distributional variants of
one process family, not independent families.

Fit parametric variants by likelihood and the empirical variant by a frozen
ECDF. Generation is linear. Renewal models improve the IAT marginal but cannot
reproduce serial dependence or regimes without another layer.

Primary traffic evidence: Arfeen et al.,
[“The role of the Weibull distribution in modelling traffic in Internet access and backbone core networks”](https://www.sciencedirect.com/science/article/pii/S1084804519301547),
*Journal of Network and Computer Applications*, 2019.

### Directional marked Hawkes process

Use one process per outbound/inbound direction, with self-excitation and
cross-excitation after prior packets. Frame length may be a conditional mark or
a small mark category that influences excitation. Exponential kernels permit
recursive likelihood evaluation and bounded simulation.

This is the strongest direct candidate for request/response bursts and
cross-direction triggering. It requires strict branching-ratio stability,
prehistory and window-edge rules, a very small kernel vocabulary, and strong
regularization. A flexible Hawkes model can confuse externally driven rate
changes with self-excitation.

Primary evidence: Hawkes,
[“Spectra of Some Self-Exciting and Mutually Exciting Point Processes”](https://doi.org/10.1093/biomet/58.1.83),
*Biometrika*, 1971; Hawkes and Oakes,
[cluster-process representation](https://doi.org/10.2307/3212693), 1974.

### Hidden semi-Markov or hidden Markov-renewal model

A hidden semi-Markov model adds explicit non-geometric state dwell
distributions to the packet-level HMM. A hidden Markov-renewal variant instead
conditions general IAT distributions on hidden state transitions. Either can
model persistent workload phases and state-dependent timing and marks more
faithfully than an ordinary HMM or MMPP.

Only one of these overlapping representations should be implemented initially.
State count, maximum dwell duration, and emission families must be small and
fixed because typical inference costs `O(N K^2 D_max)` and is prone to local
optima.

Primary evidence: Levinson,
[“Continuously Variable Duration Hidden Markov Models for Automatic Speech Recognition”](https://doi.org/10.1016/S0885-2308(86)80009-2),
1986; Pyke,
[“Markov Renewal Processes: Definitions and Preliminary Properties”](https://doi.org/10.1214/aoms/1177704863),
1961.

### Small K-state MMPP with state-conditioned marks

Extend the existing two-state MMPP to three or four ordered-rate states and
condition the joint direction/frame-length distribution on the latent state.
This is the most direct reuse of the current generator and can distinguish idle,
interactive, burst, and sustained-transfer regimes.

Adding states alone is not sufficient evidence of improvement. State order,
initialization, label identifiability, and matrix-exponential likelihood must be
explicit; an unrestricted state count is not suitable.

Primary evidence: Fischer and Meier-Hellstern,
[“The Markov-Modulated Poisson Process (MMPP) Cookbook”](https://doi.org/10.1016/0166-5316(93)90035-S),
*Performance Evaluation*, 1993.

### Hierarchical MMPP

A hierarchy or superposition of small MMPP components represents several rate
modulation scales while retaining finite-state Markov structure. It is a
tractable pseudo-long-range-dependent model for multiscale rate dynamics.

It cannot create true asymptotic long-range dependence, and a large hierarchy
would be difficult to identify and serialize. The number of layers and states
must remain small.

Primary evidence: Muscariello et al.,
[“Markov Models of Internet Traffic and a New Hierarchical MMPP Model”](https://doi.org/10.1016/j.comcom.2005.02.012),
*Computer Communications*, 2005.

### Poisson Pareto Burst Process

PPBP creates bursts at Poisson epochs, with Pareto-distributed burst durations
and a transmission rate while each burst is active. Superposed bursts provide a
parsimonious traffic-specific model for long-range dependence and multiscale
busy periods.

Reliable Pareto-tail estimation requires long captures and threshold/censoring
rules. The model also needs a declared conversion from active burst rate to
packet timestamps and conditional direction/length marks.

Primary evidence: Zukerman, Neame, and Addie,
[“Internet Traffic Modeling and Future Technology Implications”](https://doi.org/10.1109/INFCOM.2003.1208709),
*IEEE INFOCOM*, 2003.

### Heavy-tailed ON/OFF source superposition

Multiple latent sources alternate between active packet production and silence;
heavy-tailed ON or OFF durations can generate aggregate self-similarity and
long-range dependence. It provides a mechanistic explanation for traffic that
remains bursty across scales.

Source count, ON/OFF segmentation, tail exponents, and within-ON packet rules
are only weakly identified from aggregate short captures. It should be claimed
only as an aggregate model, not as recovered source behavior.

Primary evidence: Willinger, Taqqu, Sherman, and Wilson,
[“Self-Similarity Through High-Variability”](https://doi.org/10.1109/90.554723),
*IEEE/ACM Transactions on Networking*, 1997.

### Fractional Sum–Difference and MFSD models

FSD/MFSD models transform a combination of fractional Gaussian noise and a
short-memory component into positive packet IATs, commonly with a Weibull
marginal. They provide a direct event-timing route to short- and long-scale
dependence without requiring a wavelet generator.

They need long captures, careful low-frequency estimation, and an additional
joint direction/length mark law. The fitted model is more specialized and less
transparent than ACD or a small hidden-state model.

Primary evidence: Anderson, Cleveland, and Xi,
[“Multifractal and Gaussian Fractional Sum–Difference Models for Internet Traffic”](https://doi.org/10.1016/j.peva.2016.11.001),
*Performance Evaluation*, 2017.

### Poisson or negative-binomial INGARCH

INGARCH and related observation-driven count models recursively update a
positive conditional packet-count intensity from past counts and intensities.
They are the most natural AR-like model for nonnegative packet counts and can
capture short-run rate clustering.

They require a fixed bin width and a separate, validated packet-disaggregation
and conditional-mark contract. The Poisson form may underfit overdispersion;
the negative-binomial form adds another fitted dispersion parameter.

Primary evidence: Ferland, Latour, and Oraichi,
[“Integer-Valued GARCH Process”](https://doi.org/10.1111/j.1467-9892.2006.00496.x),
*Journal of Time Series Analysis*, 2006.

### Copula-based mark layer

A low-dimensional empirical, Gaussian, or Student-t copula can preserve
contemporaneous or lagged dependence between IAT and frame length, conditional
on direction or latent regime. It is not a complete arrival model and must be
paired with renewal, ACD, HMM, MMPP, MAP, or Hawkes timing.

Discrete direction requires conditioning or a discrete construction rather
than treating it as a continuous copula margin. High-dimensional vine copulas
are not justified for the prototype.

Traffic-specific evidence: Stoudt et al.,
[“Modeling Internet Traffic Generations Based on Users and Activities for Telecommunication Applications”](https://scholarworks.smith.edu/mth_facpubs/34/),
2016.

## Traffic models — Optional

### Cox process

A Cox process generates a random latent intensity and then samples a
conditionally Poisson process. It represents environmental rate variability
and overdispersion but is difficult to distinguish from MMPP, Poisson clusters,
or Hawkes excitation using one finite trace. A small, explicitly parameterized
latent-intensity family could be evaluated after NHPP.

Primary evidence: Cox,
[“Some Statistical Methods Connected with Series of Events”](https://doi.org/10.1111/j.2517-6161.1955.tb00188.x),
1955.

### Exogenous Poisson-cluster or Neyman–Scott process

Latent parent events generate a random number of packet offspring at random
offsets, with direction/length marks optionally conditioned on the cluster. It
is useful when bursts represent externally initiated tasks rather than
self-excitation.

Parent allocation is unobserved, edge censoring matters, and a cluster process
may be observationally indistinguishable from Hawkes or Cox alternatives at the
available sample size.

Primary evidence: Neyman and Scott,
[“Statistical Approach to Problems of Cosmology”](https://doi.org/10.1111/j.2517-6161.1958.tb00272.x),
1958.

### Bartlett–Lewis packet/flow cluster process

Model flow starts as parent points and packet arrivals within each flow as a
finite offspring cluster. Direction and frame length can be attached as packet
marks, allowing aggregate packet generation from the present trace even when
the latent parents cannot be assigned real flow headers.

The model relies on approximate independence between flows and needs a bounded
cluster-lifetime and intra-cluster arrival family. Parent allocation is latent,
and congestion, upstream shaping, or shared bottlenecks can violate the
independence assumption. A current-schema implementation must describe the
parents as latent clusters rather than claim recovered flow identity.

Primary evidence: Hohn, Veitch, and Abry,
[“Cluster Processes: A Natural Language for Network Traffic”](https://doi.org/10.1109/TSP.2003.814460),
2003; Hohn, Veitch, and Ye,
[“Splitting and Merging of Packet Traffic: Measurement and Modelling”](https://doi.org/10.1016/j.peva.2005.07.025),
2005.

### Self-correcting point process

A self-correcting intensity rises with elapsed time and falls after each event.
It is a useful specialized competitor for polling, heartbeat, and other
anti-clustered traffic, but it is unsuitable as a general model for the bursty
captures that motivate the current work.

Primary evidence: Isham and Westcott,
[“A Self-Correcting Point Process”](https://doi.org/10.1016/0304-4149(79)90008-5),
1979.

### Transform–Expand–Sample

TES constructs a correlated stationary sequence with an arbitrary marginal and
controllable autocorrelation. It can generate IAT and frame-length series
cheaply after fitting, with direction handled by a separate categorical chain.

Traditional TES fitting is heuristic or interactive rather than a clean
likelihood procedure. A Trafficlab implementation would first need a fully
automatic deterministic fitting rule and a joint-mark design.

Primary evidence: Melamed,
[“TES: A Class of Methods for Generating Autocorrelated Uniform Variates”](https://doi.org/10.1287/ijoc.3.4.317),
*ORSA Journal on Computing*, 1991.

### INAR count model

An INAR process uses binomial thinning and integer innovations, so packet counts
remain nonnegative integers while retaining short-memory correlation. It is a
useful simpler count baseline below INGARCH.

It models neither exact packet timestamps nor arbitrary byte totals. A fixed
binning, packet-disaggregation rule, and joint mark generator remain necessary.

Primary evidence: Al-Osh and Alzaid,
[“First-Order Integer-Valued Autoregressive Process”](https://doi.org/10.1111/j.1467-9892.1987.tb00438.x),
1987.

### ARFIMA/FARIMA rate model

ARFIMA combines finite-order short-memory terms with fractional differencing for
hyperbolic long memory. It may model a transformed packet- or byte-rate series
when sufficient low-frequency evidence exists.

It produces a real-valued binned series, not events. Positivity transforms,
finite-window initialization, bin choice, and packetization can materially
change the intended covariance.

Primary evidence: Hosking,
[“Fractional Differencing”](https://doi.org/10.1093/biomet/68.1.165),
*Biometrika*, 1981.

### Fractional Gaussian noise or fractional Brownian rate

Fractional Gaussian noise can model a stationary self-similar binned rate,
while fractional Brownian motion models cumulative workload. The Hurst
parameter gives direct long-memory control and the process is analytically
tractable.

Gaussian increments are signed. Clipping or exponentiating them to obtain a
positive rate changes the covariance, and neither construction provides exact
packet times or marks without another layer.

Primary traffic evidence: Norros,
[“A Storage Model with Self-Similar Input”](https://doi.org/10.1007/BF01158964),
*Queueing Systems*, 1994.

### Linear or generalized state-space count/rate model

A small latent linear dynamical system can represent smoothly changing traffic
rate and tolerate missing bins. A generalized observation model or log link can
enforce positive counts.

The ordinary Kalman form is Gaussian and permits negative output. Non-Gaussian
emissions require particle, Laplace, or other approximate inference and begin
to overlap with Cox, HMM, and regime-switching models.

Primary foundation: Kalman,
[“A New Approach to Linear Filtering and Prediction Problems”](https://doi.org/10.1115/1.3662552),
1960.

### Markov-switching autoregressive/count model

A hidden Markov chain selects different AR means, coefficients, variances, or
count-intensity parameters. It can model quiet/busy binned regimes and
within-regime persistence.

It overlaps conceptually with MMPP and packet HMM while losing their native
continuous-time event semantics. State and order selection, label switching,
and packet disaggregation add complexity.

Primary foundation: Hamilton,
[“A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle”](https://doi.org/10.2307/1912559),
1989.

### Non-wavelet multiplicative-cascade multifractal model

A binomial or other multiplicative cascade generates positive multiscale packet
or byte mass and can reproduce scale-dependent intermittency beyond a single
Hurst exponent.

Scaling-range and multiplier inference are fragile on short captures. The
model is binned and needs packet timestamp and mark generation. Wavelet-based
fitting or synthesis remains excluded even if the underlying cascade is
otherwise classical.

Primary evidence: Feldmann, Gilbert, and Willinger,
[“Data Networks as Cascades”](https://doi.org/10.1145/285237.285256),
1998; Yang et al.,
[“A New Multifractal Network Traffic Model”](https://doi.org/10.1016/S0960-0779(01)00159-X),
2002.

## Traffic models — Expensive (special requirements)

### Log-Gaussian Cox process

An LGCP places a Gaussian-process prior on log intensity, allowing a flexible,
positive, correlated stochastic rate. It can represent multiscale burstiness,
but kernel choice contributes substantial model freedom and dense inference on
`B` grid points costs `O(B^3)` time and `O(B^2)` memory without approximation.

It is compatible with the present trace in principle but is not a suitable
early addition to a one-process prototype with already expensive model search.

Primary evidence: Møller, Syversveen, and Waagepetersen,
[“Log Gaussian Cox Processes”](https://doi.org/10.1111/1467-9469.00115),
1998.

### Marked Markovian Arrival Process

A MAP uses matrices for latent transitions without arrivals and transitions
with arrivals. A marked MAP can attach direction and size category to distinct
arrival-transition matrices. It strictly generalizes MMPP and can reproduce
non-exponential correlated arrivals.

Matrix parameterization, identifiability, numerical likelihood, serialization,
and validity repair are substantially more difficult than for a small MMPP.
The full matrices must not be evolved directly by the genetic algorithm.

Primary evidence: Neuts,
[“A Versatile Markovian Point Process”](https://doi.org/10.2307/3213143),
*Journal of Applied Probability*, 1979.

### Batch Markovian Arrival Process and compound Poisson

BMAP extends MAP with matrices for transitions that emit batches of different
sizes. Compound Poisson is the simpler one-phase batch special case. These can
represent correlated bulk arrivals and exact co-timestamp packets.

Timestamp resolution can make unrelated packets appear simultaneous. Batches
must therefore be justified against capture precision before implementation;
otherwise the fitted model would reproduce a measurement artifact. Batch
support also multiplies matrix and validation complexity.

Primary evidence: Lucantoni,
[“New Results on the Single Server Queue with a Batch Markovian Arrival Process”](https://doi.org/10.1080/15326349108807174),
1991.

### Harpoon flow model

Harpoon learns application-independent TCP/UDP file-transfer flow
distributions, address-space characteristics, and temporal load from NetFlow or
packet traces, then uses live endpoints and protocol stacks to emit traffic.

Trafficlab would need valid IP/transport headers, flow identity, endpoint
populations, and live generation processes. This conflicts with the current
offline one-process PCAPNG synthesis boundary.

Primary evidence: Sommers and Barford,
[“Self-Configuring Network Traffic Generation”](https://doi.org/10.1145/1028788.1028798),
*ACM IMC*, 2004.

### Swing structural workload model

Swing extracts user, application, and network characteristics and generates
closed-loop traffic through real protocol stacks and an emulated network. It
can reproduce network-responsive burstiness and supports counterfactual path
conditions.

It requires real headers, application classification, multiple endpoints,
network emulation, and operating-system TCP behavior. Those requirements are a
substantial architecture change rather than another Trafficlab model family.

Primary evidence: Vishwanath and Vahdat,
[“Swing: Realistic and Responsive Network Traffic Generation”](https://doi.org/10.1109/TNET.2009.2020830),
2009.

### Tmix connection-vector model

Tmix reverse-compiles bidirectional TCP header traces into per-connection
application-data-unit and think-time vectors, then recreates socket-level
behavior using a TCP simulator and optional path emulation.

It needs TCP sequence and acknowledgement information, bidirectional flow
identity, RTT/loss state, and a simulator. None is present in generated
Trafficlab frames, and introducing them would abandon the simple offline
packet-statistics boundary.

Primary evidence: Weigle et al.,
[“Tmix: A Tool for Generating Realistic TCP Application Workloads in ns-2”](https://doi.org/10.1145/1140086.1140094),
2006.

### SURGE Web workload model

SURGE models HTTP request and file sizes, object popularity, embedded
references, temporal locality, and user idle periods. It is useful for Web
server and network capacity experiments but requires HTTP logs, URL/object
structure, server behavior, and application semantics.

Primary evidence: Barford and Crovella,
[“Generating Representative Web Workloads for Network and Server Performance Evaluation”](https://cseweb.ucsd.edu/groups/csag/html/individual/lxin/pdns/crovella-sigm98-surge.pdf),
*ACM SIGMETRICS*, 1998.

### PackMime-HTTP source model

PackMime models HTTP connections, request/response sizes, inter-request delays,
server delay, persistence, and integration with TCP simulation. It is a
validated application workload model, but it needs connection and HTTP
semantics that Trafficlab intentionally does not retain or generate.

Primary evidence: Cao et al.,
[“Stochastic Models for Generating Synthetic HTTP Source Traffic”](https://doi.org/10.1109/INFCOM.2004.1354568),
*IEEE INFOCOM*, 2004.

## Traffic models — Not recommended

### AR, ARMA, ARIMA, and SARIMA

These are useful forecasting baselines for transformed binned rates, trends,
and fixed seasonal effects, but they generate signed real-valued series rather
than packets. Rounding, clipping, inverse transforms, bin choice, and
within-bin packet placement can dominate the supposed traffic model. INAR or
INGARCH is preferable when a binned count model is required, while ACD or NHPP
preserves native event time.

Primary foundation: Box and Jenkins,
[“Some Recent Advances in Forecasting and Control”](https://doi.org/10.2307/2985674),
1968.

### VAR and VARMA

Vector autoregression could jointly model outbound/inbound packet and byte rates,
but parameter count grows as `O(p d^2)`, output can be negative, and it still
needs a packet-disaggregation layer. Directional Hawkes, a joint packet HMM, or
a multivariate count model provides a closer match to current data.

Primary foundation: Sims,
[“Macroeconomics and Reality”](https://doi.org/10.2307/1912017),
1980.

### Fluid ON/OFF model

Finite Markov ON/OFF sources emitting continuous fluid are analytically useful
for queues, but they do not define packet times, sizes, or directions.
Packetization becomes the effective model, while exponential ON/OFF durations
also fail to provide genuine long-range dependence.

Primary evidence: Anick, Mitra, and Sondhi,
[“Stochastic Theory of a Data-Handling System with Multiple Sources”](https://doi.org/10.1002/j.1538-7305.1982.tb03089.x),
1982.

### Alpha-stable and fractional-Lévy traffic

Alpha-stable self-similar processes can reproduce very heavy bursts, but for
stability index below two their variance is undefined. That conflicts with the
current autocorrelation and variance-oriented diagnostics, and signed aggregate
increments still require a positivity and packetization rule. Adopting this
family would require a separate scientific evaluation contract.

Primary evidence: Gallardo et al.,
[“Use of Alpha-Stable Self-Similar Stochastic Processes for Modeling Traffic in Broadband Networks”](https://doi.org/10.1016/S0166-5316(99)00070-X),
2000.

### Chaotic-map traffic model

Deterministic intermittent maps can create ON/OFF sequences with apparent
long-range dependence using few parameters. Parameter inference, noise
sensitivity, interpretation, and queue analysis are difficult, and the model
offers no demonstrated advantage over ON/OFF, PPBP, or FSD for this project.

Primary evidence: Mondragón, Arrowsmith, and Pitts,
[“Chaotic Maps for Traffic Modelling and Queueing Performance Analysis”](https://doi.org/10.1016/S0166-5316(00)00047-X),
2001.

### Multifractal Wavelet Model and other wavelet generators

MWM can efficiently synthesize positive non-Gaussian multiscale traffic, but
the approved architecture explicitly excludes wavelets. It must not be added
without an intentional scope change.

Primary evidence: Riedi, Crouse, Ribeiro, and Baraniuk,
[“A Multifractal Wavelet Model with Application to Network Traffic”](https://doi.org/10.1109/18.761337),
1999.

### Neural and diffusion traffic generators

GAN, VAE, recurrent neural, transformer, score-based, and diffusion generators
are outside the classical-model scope. Their ability to fit a training corpus
does not compensate for the loss of the intended interpretability and bounded
one-person research architecture.

### Packet or flow replay

Replay reproduces recorded events or timings rather than sampling a fitted
stochastic family. It cannot demonstrate model generalization and is explicitly
excluded from the architecture.

### D-ITG as a model family

D-ITG is a capable distributed live traffic-generation platform that can be
configured with several stochastic laws, but it is not itself a fitted traffic
model. It also conflicts with the one-process offline generator boundary. Its
individual stochastic laws may be evaluated independently under the earlier
categories.

Primary platform evidence: Avallone et al.,
[“Performance Evaluation of an Open Distributed Platform for Realistic Traffic Generation”](https://doi.org/10.1016/j.peva.2004.10.012),
2005.

## Model-family relationships

The following relationships prevent aliases and restricted cases from being
mistaken for independent candidates:

- homogeneous Poisson is both exponential renewal and constant-intensity NHPP;
- Gamma, Erlang, Weibull, lognormal, Pareto, hyperexponential, phase-type, and
  empirical IAT laws are renewal variants;
- interrupted Poisson is a restricted MMPP with a zero-rate state;
- two-state MMPP is a restricted K-state MMPP;
- MMPP is a restricted MAP, and MAP is a restricted BMAP;
- compound Poisson is a one-phase batch-arrival special case;
- directional and marked Hawkes are extensions of one Hawkes family;
- hidden Markov-renewal and hidden semi-Markov models substantially overlap and
  should initially be treated as alternative representations, not two required
  implementations;
- a copula is a dependence/mark layer, not a complete arrival process;
- Harpoon, Swing, Tmix, SURGE, PackMime, and D-ITG are workload/generation
  systems rather than drop-in implementations of the current canonical model
  interface.

## Similarity-method compatibility boundary

Similarity candidates compare the same normalized reference and generated
canonical traces over a shared observation window. They may operate on raw
packet events, IAT and frame-length sequences, direction-conditioned samples,
or deterministic fixed-window features derived from those columns. A method is
not a runtime similarity component until it has a bounded `[0, 1]` score
mapping, diagnostics, preconditions, configuration limits, hand-checked tests,
and a registry entry.

Packet observations and adjacent windows are serially dependent. Classical
IID hypothesis-test p-values are therefore not valid by default. Candidate
statistics should be interpreted as descriptive distances, or inference should
use a predeclared block bootstrap, block permutation, or separated time-block
protocol. Bins, feature scaling, kernels, thresholds, and approximation seeds
must be frozen from configuration or reference-only training data rather than
tuned against the generated trace being scored.

Some candidates are model-adequacy or research diagnostics rather than
symmetric trace-to-trace similarities. Their entries label that distinction so
they are not silently inserted into genetic fitness.

## Similarity methods — Required

| Candidate | Representation and value | Limitations and intended role | Primary evidence |
|---|---|---|---|
| **Two-sample Cramér–von Mises** | Compare the complete ECDF discrepancy for frame length and IAT, separately by direction where sample size permits. It complements the maximum-only KS statistic at sorting cost. | Less tail-sensitive than Anderson–Darling. Use the statistic descriptively; serial dependence and ties invalidate an ordinary IID p-value. **Fitness candidate.** | Anderson, [“On the Distribution of the Two-Sample Cramér–von Mises Criterion”](https://doi.org/10.1214/aoms/1177704477), 1962. |
| **Anderson–Darling** | Tail-weighted ECDF comparison for frame length and IAT, including long silences and unusually large frames. | Sparse tails and tied values need declared handling. It should complement rather than replace an all-distribution statistic. **Fitness candidate.** | Scholz and Stephens, [“K-Sample Anderson–Darling Tests”](https://doi.org/10.1080/01621459.1987.10478517), 1987. |
| **Jensen–Shannon divergence** | Compare exact frame-length PMFs and shared-bin IAT or direction-by-time distributions. It is symmetric, finite, and bounded after a declared logarithm base. | Results depend on common binning and pseudocount policy; it does not know that neighboring bins are close. **Fitness and decomposition candidate.** | Lin, [“Divergence Measures Based on the Shannon Entropy”](https://doi.org/10.1109/18.61115), 1991. |
| **Approximate joint MMD** | Apply a characteristic product kernel to fixed-window or lag vectors such as `(log1p(IAT), log(frame_length), direction)` and adjacent-packet pairs. Standardize only continuous coordinates; use a declared delta/Hamming kernel for unordered direction. Random features or deterministic block estimates avoid whole-trace quadratic cost. | Kernel, bandwidth, scaling, categorical handling, approximation dimension, and seed materially determine sensitivity. Overlapping windows require blocked calibration. **Highest-value joint-distribution fitness candidate.** | Gretton et al., [“A Kernel Two-Sample Test”](https://jmlr.org/papers/v13/gretton12a.html), 2012. |
| **Fano- and Allan-factor curves** | Compare packet-count dispersion and adjacent-window rate variation over logarithmically spaced window sizes. These expose clustering and scale-dependent overdispersion that mean-rate comparison misses. | Large scales have few windows and are sensitive to nonstationarity and boundaries. Compare the full frozen curves rather than only one fitted exponent. **Multiscale diagnostic and possible fitness candidate.** | Lowen and Teich, [“Estimation and Simulation of Fractal Stochastic Point Processes”](https://doi.org/10.1142/S0218348X95000151), 1995. |
| **Transition-matrix fidelity** | Quantize `(direction, log-size bin, log-IAT bin)` into a small frozen state vocabulary; compare transition rows, state occupancy, and dwell/run distributions. | State design and sparse rows dominate the result. Use few reference-defined bins and smoothing with explicit diagnostics. **Required structural diagnostic**, closely aligned with Markov-renewal behavior but family-neutral. | Anderson and Goodman, [“Statistical Inference about Markov Chains”](https://doi.org/10.1214/aoms/1177707039), 1957. |
| **Classical classifier two-sample test** | Label held-out fixed windows as reference/generated and train a bounded logistic regression, optionally compared with a shallow tree ensemble. Report time-blocked balanced accuracy/AUC and interpretable coefficients. | Leakage between adjacent windows and classifier flexibility inflate results. It establishes distinguishability, not which trace is better. **Required post-fit diagnostic, not initial GA fitness.** | Lopez-Paz and Oquab, [“Revisiting Classifier Two-Sample Tests”](https://openreview.net/pdf?id=SJkXfE5xx), 2017. |

## Similarity methods — Nice to have

| Candidate | Representation and value | Limitations and intended role | Primary evidence |
|---|---|---|---|
| **Hellinger distance** | Symmetric bounded distance for exact or shared-bin PMFs of size, IAT, direction, and window features. It behaves more stably near zero mass than KL. | Ignores numeric bin geometry and still depends on binning and smoothing. **Cheap fitness candidate.** | Ali and Silvey, [“A General Class of Coefficients of Divergence”](https://doi.org/10.1111/j.2517-6161.1966.tb00626.x), 1966. |
| **Total variation / normalized L1** | Direct bounded PMF discrepancy for frame size, IAT bins, direction, and multiscale count decompositions. It is transparent and closely related to the implemented multiscale L1 construction. | Treats adjacent and distant bins equally and can duplicate an existing L1 component. **Diagnostic/decomposition candidate.** | Ali and Silvey, [general divergence class](https://doi.org/10.1111/j.2517-6161.1966.tb00626.x), 1966. |
| **Energy / multivariate Cramér distance** | Compare standardized multivariate packet or fixed-window vectors without selecting a kernel. It detects joint differences missed by marginal scores. | Exact pairwise evaluation is `O(N^2)`; use a deterministic subsample, block estimator, or window-level sample with disclosed error. **Joint fitness candidate below approximate MMD.** | Baringhaus and Franz, [“On a New Multivariate Two-Sample Test”](https://doi.org/10.1016/S0047-259X(03)00079-4), 2004. |
| **Point-process or binned-rate spectrum** | Compare smoothed log power, band power, or periodograms for event trains and direction-separated packet/byte rates. It captures periodicity and frequency structure beyond selected ACF lags. | Requires a fixed bin width or point-process estimator, taper, smoothing, and frequency grid; short traces have noisy spectra. **Frequency-domain diagnostic and possible fitness candidate.** | Bartlett, [“The Spectral Analysis of Point Processes”](https://doi.org/10.1111/j.2517-6161.1963.tb00508.x), 1963. |
| **Variance-time curve** | Compare packet/byte-count variance or variance-to-mean ratio across aggregation scales, separately by direction. | A single Hurst slope is fragile under finite samples and trends; retain the curve and scale-wise uncertainty. **Multiscale diagnostic.** | Leland et al., [“On the Self-Similar Nature of Ethernet Traffic”](https://doi.org/10.1109/90.282603), 1994. |
| **Burstiness and IAT-memory signatures** | Compare the normalized IAT burstiness coefficient and lagged IAT memory with blocked confidence intervals. This separates dispersion from ordering at negligible cost. | It is only a low-dimensional signature; distinct processes can share the same values. **Diagnostic, not sole fitness.** | Goh and Barabási, [“Burstiness and Memory in Complex Systems”](https://doi.org/10.1209/0295-5075/81/48002), 2008. |
| **Thresholded burst/run summaries** | At frozen scales and reference-defined thresholds, compare burst duration, mass, peak rate, inter-burst gaps, and run-length ECDFs. | Threshold, bin, and boundary rules can manufacture conclusions; report multiple predeclared scales. **Interpretable structural diagnostic.** | Leland et al., [Ethernet traffic study](https://doi.org/10.1109/90.282603), 1994. |
| **Direction/activity runs statistic** | Compare run count and run-length distribution in the direction sequence or a frozen binary activity sequence. It directly detects clumping versus alternation. | Sensitive to class imbalance and tests only a narrow structural property. Ordinary IID calibration is inappropriate for packet sequences. **Cheap diagnostic.** | Wald and Wolfowitz, [“On a Test Whether Two Samples Are from the Same Population”](https://doi.org/10.1214/aoms/1177731909), 1940. |
| **Permutation entropy** | Compare ordinal-pattern distributions in IAT, frame-length, or binned-rate sequences. It captures temporal ordering beyond linear ACF and is invariant to monotone transforms. | Tie handling and embedding dimension are consequential; `m!` patterns require enough observations. **Lightweight sequential diagnostic.** | Bandt and Pompe, [“Permutation Entropy”](https://doi.org/10.1103/PhysRevLett.88.174102), 2002. |
| **Low-order n-gram cross-entropy** | Quantize a frozen `(direction, size, IAT)` state and compare reference-to-generated and generated-to-reference order-one or order-two predictive cross-entropy against a reference-heldout baseline. | Binning, smoothing, and order control sparsity; higher orders are inappropriate for current sample sizes. **Sequential diagnostic and possible score.** | Kullback and Leibler, [“On Information and Sufficiency”](https://doi.org/10.1214/aoms/1177729694), 1951. |
| **Conditional-intensity time-rescaling** | For a fitted model exposing conditional intensity, transform event intervals and test whether residuals behave as unit exponential and independent. This evaluates model calibration directly rather than comparing two realizations. | Requires a model-specific intensity and is neither symmetric nor available for every current family. **High-value model-adequacy diagnostic, never a shared trace fitness component.** | Brown et al., [“The Time-Rescaling Theorem and Its Application to Neural Spike Train Data Analysis”](https://doi.org/10.1162/08997660252741149), 2002. |

## Similarity methods — Optional

| Candidate | Representation and value | Limitations and intended role | Primary evidence |
|---|---|---|---|
| **Kullback–Leibler divergence** | Directional discrepancy for exact or binned PMFs; strongly penalizes generated support that misses probable reference events. | Asymmetric and infinite at unsupported mass without a declared pseudocount; highly bin-dependent. **Diagnostic only unless zero policy is scientifically fixed.** | Kullback and Leibler, [“On Information and Sufficiency”](https://doi.org/10.1214/aoms/1177729694), 1951. |
| **Pearson chi-square divergence** | Highlights underrepresented frame-size or IAT bins relative to a reference PMF. | Division by small expected mass is unstable and sparse bins violate common asymptotic interpretations. **Support diagnostic.** | Ali and Silvey, [general divergence class](https://doi.org/10.1111/j.2517-6161.1966.tb00626.x), 1966. |
| **Kuiper statistic** | Compare cyclic phase, such as time modulo a scientifically justified period; invariant to the chosen start of the circle. | Not appropriate for ordinary linear IAT or elapsed-time comparison and requires a defensible period. **Niche cyclic diagnostic.** | Kuiper, [“Tests Concerning Random Points on a Circle”](https://doi.org/10.1016/S1385-7258(60)50006-0), 1960. |
| **Empirical-copula two-sample tests** | Compare dependence structures for `(IAT, frame length)` or lag pairs after removing marginal effects, using sup or integrated empirical-copula statistics. | Frame sizes and direction are discrete, creating ties outside the standard continuous-margin theory; dependent bootstrap is expensive. **Research diagnostic.** | Rémillard and Scaillet, [“Testing for Equality between Two Copulas”](https://doi.org/10.1016/j.jmva.2008.05.004), 2009. |
| **Distance-correlation signature** | Within each trace, measure nonlinear dependence between IAT, size, direction encodings, and adjacent values; compare the resulting signature vectors. | It is not itself a two-sample distance, and exact evaluation is quadratic. Equal signatures do not imply equal joint distributions. **Dependence diagnostic.** | Székely, Rizzo, and Bakirov, [“Measuring and Testing Dependence by Correlation of Distances”](https://doi.org/10.1214/009053607000000505), 2007. |
| **HSIC signature** | Kernel independence signatures for IAT–size, direction–size, and adjacent-packet relationships, with product kernels for mixed data. | Kernel selection and exact quadratic cost mirror MMD concerns; it is not a complete trace comparison. **Dependence diagnostic.** | Gretton et al., [“Measuring Statistical Dependence with Hilbert-Schmidt Norms”](https://doi.org/10.1007/11564089_7), 2005. |
| **Spectral power-law slope** | Compare a preregistered low-frequency `1/f^beta` slope for counts or IAT. | One slope discards most spectral information and is unreliable on short, nonstationary traces. **Secondary diagnostic only.** | Lowen and Teich, [“Fractal Renewal Processes Generate 1/f Noise”](https://doi.org/10.1109/18.259653), 1993. |
| **Detrended fluctuation analysis** | Compare DFA scaling curves or exponents for integrated binned traffic while reducing sensitivity to polynomial trends. | Crossover selection, finite length, and nonlinear trends can create spurious exponents. **Research diagnostic, not fitness.** | Peng et al., [“Mosaic Organization of DNA Nucleotides”](https://doi.org/10.1103/PhysRevE.49.1685), 1994. |
| **Rescaled-range/Hurst signature** | Summarize apparent long memory in binned counts, bytes, or IAT. | Biased by trends and finite samples; the same Hurst value does not identify a process. **Low-priority diagnostic.** | Hurst, [“Long-Term Storage Capacity of Reservoirs”](https://doi.org/10.1061/TACEAT.0006518), 1951. |
| **Higuchi fractal dimension** | Compare roughness of uniformly binned rate or byte series. | Sensitive to maximum scale and has weak direct traffic interpretation; not applicable to raw event times. **Dashboard/research diagnostic.** | Higuchi, [“Approach to an Irregular Time Series on the Basis of the Fractal Theory”](https://doi.org/10.1016/0167-2789(88)90081-4), 1988. |
| **Sample entropy** | Compare repeatability of short patterns in IAT, size, or binned-rate sequences. | Embedding dimension, tolerance, minimum sample size, and naive quadratic cost need strict bounds. **Optional sequence diagnostic.** | Richman and Moorman, [“Physiological Time-Series Analysis Using Approximate Entropy and Sample Entropy”](https://doi.org/10.1152/ajpheart.2000.278.6.H2039), 2000. |
| **Lempel–Ziv complexity/entropy rate** | Compare novelty and repetition after versioned quantization of direction, size, IAT, or counts. | Finite-sample bias and symbolization policy dominate results. **Optional symbolic diagnostic.** | Ziv and Lempel, [“Compression of Individual Sequences via Variable-Rate Coding”](https://doi.org/10.1109/TIT.1978.1055934), 1978. |
| **Multiscale entropy** | Compare sample entropy after coarse-graining over several frozen scales. | Sample-hungry and expensive at coarse scales; partly duplicates multiscale-rate intent. **Research diagnostic.** | Costa, Goldberger, and Peng, [“Multiscale Entropy Analysis of Complex Physiologic Time Series”](https://doi.org/10.1103/PhysRevLett.89.068102), 2002. |
| **Constrained dynamic time warping** | Align fixed-bin count or byte-rate shapes under a bounded temporal warp and expose the alignment path. | Can hide genuinely wrong timing, costs `O(B^2)`, and compares phase alignment between stochastic realizations rather than their laws. **Selected-window diagnostic only.** | Sakoe and Chiba, [“Dynamic Programming Algorithm Optimization for Spoken Word Recognition”](https://doi.org/10.1109/TASSP.1978.1163055), 1978. |
| **Victor–Purpura event-train distance** | Edit distance on packet times with insertion/deletion cost and a tunable movement cost, separating count mismatch from timing jitter. | Parameter choice and `O(NM)` cost make full packet traces impractical; random phase is heavily penalized. **Downsampled/local-window diagnostic.** | Victor and Purpura, [“Nature and Precision of Temporal Coding in Visual Cortex”](https://doi.org/10.1152/jn.1996.76.2.1310), 1996. |
| **van Rossum event-train distance** | Convolve event trains with an exponential kernel and compare them in L2; the kernel time constant gives a clear scale. | Time-constant and boundary choices matter, and smoothing can hide burst detail. **Multiscale selected-window diagnostic.** | van Rossum, [“A Novel Spike Distance”](https://doi.org/10.1162/089976601300014321), 2001. |
| **ISI distance** | Parameter-free time-resolved comparison based on instantaneous local inter-event intervals. | Edge conventions matter and two valid stochastic realizations may be penalized despite matching laws. **Selected-window diagnostic.** | Kreuz et al., [“Monitoring Spike Train Synchrony”](https://doi.org/10.1016/j.jneumeth.2007.05.031), 2007. |
| **SPIKE distance** | Parameter-free nearest-event timing discrepancy with a time-resolved profile. | Semantics emphasize synchrony, not stochastic traffic fidelity; costly on large packet traces. **Selected-window diagnostic.** | Kreuz et al., [“Measuring Multiple Spike Train Synchrony”](https://doi.org/10.1016/j.jneumeth.2010.11.020), 2011. |
| **k-nearest-neighbor precision and recall** | On frozen standardized window features, estimate generated fidelity to the reference manifold and reference-mode coverage by generated samples. | Representation, `k`, sample size, and quadratic neighbor search control results; no learned image embedding should be used. **Post-fit diversity diagnostic.** | Kynkäänniemi et al., [“Improved Precision and Recall Metric for Assessing Generative Models”](https://proceedings.neurips.cc/paper/2019/hash/0234c510bc6d908b28c70ff313743079-Abstract.html), 2019. |
| **Density and coverage** | Refine nearest-neighbor precision/recall on the same frozen traffic-window feature space to expose local fidelity and mode coverage. | Still representation- and `k`-dependent and potentially quadratic. **Phase-two diversity diagnostic.** | Naeem et al., [“Reliable Fidelity and Diversity Metrics for Generative Models”](https://proceedings.mlr.press/v119/naeem20a.html), 2020. |
| **TSTR/TRTS predictive utility** | Define an operational task such as next-window count, next burst, or next symbolic state; train a classical predictor on synthetic and test real, then reverse it and compare with real-to-real. | Invalid without a frozen task, horizon, split, and baseline; measures usefulness rather than general resemblance. **Goal-specific validation only.** | Esteban, Hyland, and Rätsch, [“Real-valued (Medical) Time Series Generation with Recurrent Conditional GANs”](https://arxiv.org/abs/1706.02633), 2017; use only its protocol with classical predictors. |

## Similarity methods — Expensive (special requirements)

| Candidate | Special requirement and value | Why deferred | Primary evidence |
|---|---|---|---|
| **Flow-summary marginal comparisons** | Requires valid 5-tuple flow identity and a frozen timeout, then compares flow duration, packets, bytes, direction ratios, and IAT/size summaries with AD or related distances. | Current generated frames have no IP/transport identity, so flow fidelity cannot be claimed. | Anderson and Darling, [goodness-of-fit foundation](https://doi.org/10.1214/aoms/1177729437), 1952; flow-generation context in [Harpoon](https://doi.org/10.1145/1028788.1028798), 2004. |
| **Joint flow-vector Energy/MMD** | Standardized flow vectors can test joint duration, volume, rate, and direction behavior once real flows exist. | Requires the same L3/L4 expansion and exact quadratic estimators need deterministic approximation or subsampling. | Székely and Rizzo, [“Energy Statistics: A Class of Statistics Based on Distances”](https://doi.org/10.1016/j.jspi.2013.03.018), 2013; [Gretton et al.](https://jmlr.org/papers/v13/gretton12a.html), 2012. |
| **Flow-tail preservation** | Compare threshold-stability plots and blocked intervals for tail indexes of flow bytes, packets, duration, and peak rate. | Needs many valid flows, header generation, censoring rules, and a predeclared threshold sweep. | Hill, [“A Simple General Approach to Inference about the Tail of a Distribution”](https://doi.org/10.1214/aos/1176343247), 1975. |
| **Flow arrivals and concurrency** | Compare new-flow and active-flow counts, multiscale dispersion, and concurrency profiles. | Flow keys and timeouts are absent from generated artifacts and substantially change the canonical schema. | Sommers and Barford, [Harpoon flow methodology](https://doi.org/10.1145/1028788.1028798), 2004. |
| **Marked point-process rescaling** | Jointly test event intensity and the conditional direction/size mark distribution for models exposing both laws. | Every model needs a common evaluable conditional intensity and mark interface; current empirical and latent-state families do not share one. | Vere-Jones and Schoenberg, [“Rescaling Marked Point Processes”](https://doi.org/10.1080/0266476042000248818), 2004. |
| **GAN-train/GAN-test analogue** | With multiple trusted workload labels, train a classical classifier on generated windows and test real, then reverse it to separate utility and coverage. | Current captures do not provide a validated multi-workload label task; direction is not an adequate label. | Shmelkov, Schmid, and Alahari, [“How Good Is My GAN?”](https://www.ecva.net/papers/eccv_2018/papers_ECCV/html/Konstantin_Shmelkov_How_good_is_ECCV_2018_paper.php), 2018; use only the evaluation protocol. |

## Similarity methods — Not recommended

| Candidate or use | Reason | Primary evidence |
|---|---|---|
| **Wasserstein/Earth Mover distance** | Scientifically relevant in one and multiple dimensions, but optimal transport is explicitly excluded. A joint ground metric and feature scaling would also become new scientific policy. | Rubner, Tomasi, and Guibas, [“The Earth Mover's Distance as a Metric for Image Retrieval”](https://doi.org/10.1023/A:1026542806194), 2000. |
| **Sinkhorn/entropy-regularized OT** | Faster than exact OT but still excluded; regularization changes the measured object and adds a consequential epsilon parameter. | Cuturi, [“Sinkhorn Distances”](https://papers.nips.cc/paper_files/paper/2013/hash/af21d0c97db2e27e13572cbf59eb343d-Abstract.html), 2013. |
| **Sliced Wasserstein** | Projection-based OT reduces cost but can miss differences outside sampled projections and remains an optimal-transport method. | Rabin et al., [“Wasserstein Barycenter and Its Application to Texture Mixing”](https://doi.org/10.1007/978-3-642-23719-4_3), 2011. |
| **Gromov–Wasserstein** | Unnecessary when traces already share the same canonical columns; computationally heavy, non-convex, and explicitly within excluded OT. | Mémoli, [“Gromov–Wasserstein Distances and the Metric Approach to Object Matching”](https://doi.org/10.1007/s10208-011-9095-7), 2011. |
| **Unbalanced optimal transport** | Could separate moved from created packet/byte mass, but introduces additional mass-creation penalties and remains excluded OT. | Chizat et al., [“Scaling Algorithms for Unbalanced Transport Problems”](https://doi.org/10.1007/s00205-017-1146-0), 2018. |
| **Fréchet feature distance** | A Gaussian mean/covariance summary on hand-designed traffic features can hide multimodality and depends strongly on covariance conditioning. The original learned image representation is out of scope; MMD, C2ST, and density/coverage are safer. | Heusel et al., [“GANs Trained by a Two Time-Scale Update Rule”](https://papers.nips.cc/paper_files/paper/2017/hash/8a1d694707eb0fefe65871369074926d-Abstract.html), 2017. |
| **Neural discriminative or predictive score** | TimeGAN-style RNN discriminators and predictors violate the classical-only scope. The useful protocol is covered by classical C2ST and TSTR/TRTS candidates. | Yoon, Jarrett, and van der Schaar, [“Time-Series Generative Adversarial Networks”](https://proceedings.neurips.cc/paper/2019/hash/c9efe5f26cd17ba6216bbe2a7d26d490-Abstract.html), 2019. |
| **Wavelet variance, wavelet-leader, or wavelet-entropy similarity** | These can diagnose scaling traffic but the architecture explicitly excludes wavelets. PSD, Fano/Allan, and variance-time curves cover related questions without that scope change. | Riedi et al., [“A Multifractal Wavelet Model with Application to Network Traffic”](https://doi.org/10.1109/18.761337), 1999. |
| **IID packet-level p-values** | Packet events, IATs, and adjacent windows are dependent, so unmodified IID KS, AD, MMD, or classifier-test calibration gives false precision. Retain descriptive distances or use a declared blocked procedure. | Künsch, [“The Jackknife and the Bootstrap for General Stationary Observations”](https://doi.org/10.1214/aos/1176347265), 1989. |
| **Direct alignment or synchrony as mandatory fitness** | DTW, Victor–Purpura, van Rossum, ISI, and SPIKE distances can be useful on selected windows, but global fitness would penalize acceptable independent stochastic realizations and encourage replay-like phase matching. | The individual primary sources are listed in the Optional section. |
| **Single Hurst, DFA, entropy, or fractal scalar as mandatory fitness** | A single summary is non-identifying, scale-range-sensitive, and easy to optimize while other behavior degrades. Preserve curves/signatures as research diagnostics only. | The individual primary sources are listed in the Optional section. |

## Research comparison recommendation

No model family or similarity method is guaranteed to be best from its name or
expressiveness. This catalog recommends that a future external comparative
study give model candidates the same observation window, trial seeds,
reliability limits, and implemented similarity components, while applying each
candidate similarity method under one frozen protocol. Such a study should
define its own reference-only training blocks, untouched capture or time-block
holdouts, artifact semantics, and real-to-real variability baseline outside
this non-normative catalog. Its results should prefer a more expressive model
or metric only when held-out conclusions improve without incomplete
generation, unidentified parameters, invalid calibration, or excessive
runtime. The implemented whole-reference and common-window contracts are
unchanged.
