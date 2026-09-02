# Traffic Model Candidates

## Status and purpose

This document is a non-normative research catalog. It records classical traffic
model families that may be evaluated against Trafficlab's implemented models;
it does not define implemented behavior, commit future work, or track task
status. The authoritative runtime families remain Poisson empirical, Markov
Renewal, and two-state MMPP as defined in
[Traffic Models](traffic_models/README.md).

The priority labels express research value for the canonical data and artifact
contract available in this repository. `Required` therefore means
"required in the next comparative model study before making broader fidelity
claims," not "required for the present MVP to operate." A candidate becomes
part of the architecture only when its implementation, focused tests,
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
| **Required** | Highest-value additions needed for the next credible model-family comparison on the existing dumps. They fit the current canonical trace and have a bounded implementation path. |
| **Nice to have** | Strong scientific additions after the required set. They add materially different dependence but need more fitting care or evidence. |
| **Optional** | Valid classical experiments with narrower applicability, substantial overlap, or an extra packet-disaggregation contract. |
| **Expensive (special requirements)** | Scientifically valid families that need costly inference, substantially more metadata, live protocol stacks, or an architectural scope change. |
| **Not recommended** | Poor matches for the current event-level artifact and metrics, superseded by better candidates, or explicitly excluded by approved scope. |

## Required

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

## Nice to have

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

## Optional

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

## Expensive (special requirements)

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

## Not recommended

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

## Comparative evaluation rule

No family is guaranteed to be best from its name or expressiveness. This
catalog recommends that a future external comparative study give candidate
families the same observation window, trial seeds, reliability limits, and
similarity components. Such a study should define its own reference-only
training blocks, untouched capture or time-block holdouts, artifact semantics,
and real-to-real variability baseline outside this non-normative catalog. Its
results should prefer a more expressive family only when held-out fidelity
improves without incomplete generation, unidentified parameters, or excessive
runtime. The implemented whole-reference and common-window model contract is
unchanged.
