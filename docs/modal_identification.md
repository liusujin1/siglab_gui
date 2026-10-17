# Experimental Modal Identification (VIanalysis)

## Measurement requirements

This workflow identifies a single fixed force input and multiple responses.
All setups must use the same force location, force direction, boundary
conditions and phase convention. Explicitly select the force reference for
each file and the point/direction of the collocated response. A channel number
alone is not evidence that it is the driving point.

Only complex, phase-bearing FRFs are accepted. Autospectra and magnitude-only
transmissibilities remain available to the legacy single-frequency preview,
but cannot yield the absolute masses reported by this workflow. A missing
axis is omitted from the fit, never added as a zero response. Duplicate
physical point/direction mappings are rejected rather than silently averaged.

The file's existing engineering factors are applied in the same ratio used by
the VNA analysis page: response factor / force factor. The identification
table adds optional correction factors. Confirm these against sensor
calibration before interpreting kg. The response-unit selector describes the
numerator; the force-unit selector supplies the denominator. For example,
select `g` and `N` for a g/N FRF. The acceleration conversion uses 9.80665 m/s^2
per g. Mechanical response axes use the signed coefficients in the point map.

## Identification and validation

The core is SIMO common-denominator LSCF using a real-coefficient polynomial
and QR elimination of channel numerators. The bilinear frequency mapping
`z = (omega_scale + s) / (omega_scale - s)` conditions the polynomial fit
without treating the continuous-time measured FRF as an exact discrete-time
FRF. This is a local implementation of the least-squares frequency-domain
approach, not the proprietary Siemens PolyMAX implementation.

Every even model order is analyzed. Poles in the left half plane with
positive frequency and damping between 0.001% and 50% are compared against
the preceding order, with one-to-one matching. Defaults are 1% frequency
difference, 10% relative damping difference, and MAC >= 0.95. Three consecutive
orders form a recommended candidate. CMIF has only one singular value for
the single-input matrix; it is an overview, not proof of modal separation.

LSFD fits second-order receptance coefficients jointly with constant and
1/s^2 residuals. Acceleration/velocity FRFs are converted to receptance away
from DC. Least-squares weights proportional to omega^2 avoid excessive
low-frequency receptance weighting. Variable-projection nonlinear refinement
adjusts frequencies and damping near selected stable poles. The validation
view overlays the original and reconstructed receptance, with per-channel
relative accelerance-norm error and AutoMAC. Errors are fit diagnostics, not
statistical confidence intervals or proof of physical correctness.

## Mass convention

For real modes and classical damping the receptance convention is

    H_ij(w) = sum_r A_ij,r / (wn_r^2 - w^2 + 2j*zeta_r*wn_r*w)
              + upper_residual - lower_residual / w^2

With the driving-point modal component set to unity, `A_dd,r = 1/m_r`.
The fitted A coefficients are second-order modal coefficients, not the
first-order complex residues of a pole expansion. The result stores both
the absolute coefficients and `A_id/A_dd` shapes. Animation gain and camera
settings operate on display copies. The associated stiffness and damping
are `m*wn^2` and `2*m*zeta*wn`.

Mass is withheld for a nonpositive driving coefficient, a weak driving point,
significant complex coefficients (20% default), poor driving-point fits
(15% default), incomplete modal bandwidth, low provided coherence (<0.8),
nonconvergence or suspected duplicate poles. Absent coherence is reported
as unavailable, never replaced with a fabricated value. Modal mass is tied
to the chosen driving point. It is not the total structural mass, nor an
X/Y/Z effective modal mass or mass participation percentage.

## Results and compatibility

Settings and input arrays are fingerprinted. Changed inputs invalidate
export and identified-mode preview; a task whose inputs changed is discarded.
Cancel/error preserves the previous result. Worker threads receive independent
read-only arrays and never access Qt widgets. All Qt changes happen on the UI
thread. Large linear solves finish their current step before cancellation.

The `.vnamodal` v1 archive has an additive optional `identification` state
with its own schema version. It embeds all modal results, absolute coefficients,
reconstructed FRFs and diagnostics. Legacy archives without the state still
load as single-frequency previews. State is checked before replacing a page;
the restored fingerprint is checked again against the embedded data and
current settings. Stale results are saved as stale and cannot be exported as
current CSV or identified-mode GIF. CSV tables state the driving-point
reference and fingerprint; a complete reproducible session uses `.vnamodal`.

## Verification

Synthetic one- and multiple-mode systems cover separated and closely spaced
modes, known driving masses, engineering scaling, missing axes, changed grids,
noise and invalid physical inputs. Noise-free acceptance is <=0.2% frequency,
<=5% relative damping and <=2% mass error. Real files require verification of
calibration, driving-point position and boundary conditions. A plausible
animation or a low reconstruction error alone does not validate absolute mass.

References: Peeters et al., *The PolyMAX frequency-domain method*, 2004,
https://doi.org/10.1155/2004/523692; ARTeMIS Modal documentation, *Modal mass
computation*, https://www.svibs.com/resources/ARTeMIS_Modal_Help/Modal%20mass%20computation.html.

## Candidate search and local preview

The identification-settings tab contains a candidate-method selector and
collapsible search parameters. The existing Find Peaks action uses this
selection. CMIF is the default; alternatives are per-channel prominence,
overlapping-band common-denominator poles, and spectral/pole cross-checks.
CMIF here is the norm of the SIMO FRF vector after per-channel median scaling;
the scaling is confined to candidate indicators and never changes absolute
FRFs, LSFD coefficients or masses. It cannot separate repeated roots by itself.

The frequency range follows the existing automatic/manual analysis limits.
There is no fixed 10 Hz cutoff or 10 Hz spacing. Automatic bands use 25%
frequency-band overlap, independent of time-record FFT overlap settings.
Missing samples remain missing; no extrapolation or interpolation across gaps
is used. Local pole fits require contiguous common data and consecutive-order
frequency/damping stability. They are candidate evidence, not formal poles
with full shape/MAC validation. Cross-checks require both spectral and pole
evidence; CMIF plus prominence alone does not count as two independent families.
No agreement yields an explicit empty result, not a silent single-method fallback.

Candidates retain method, supporting channels, band and stability provenance in
tooltips, CSV export and additive optional `candidate_search` session state.
Search parameters have separate invalidation from formal identification:
editing prominence does not invalidate a calibrated mass, but changed input
data invalidates both. Stale candidates cannot be exported as current results.
Slower searches run in a cancellable worker on a copied snapshot; input changes
discard the returned snapshot while errors/cancellation preserve old results.

The optional local-fit action uses shared-pole single-mode variable-projection
LSFD over a local band. Every output retains a fit error and acceptance flag.
Rejected coefficients remain NaN and are excluded from the displayed shape;
there is no substitution of raw frequency-bin FRFs. This is explicitly labelled
as a non-authoritative preview, never supplies kg, never overwrites formal
results, and may be inaccurate for overlapping modes. Use formal multi-mode
identification and reconstruction checks for engineering results. The legacy
single-frequency ratio preview remains available and is labelled separately.

## Compact settings

New pages default to CMIF candidate search. The basic panel retains the driving
point/direction and one explicit confirmation covering calibrated force, fixed
test conditions and collocated/same-direction driving response. Those physical
facts are never guessed. A unique force-reference channel and its explicitly
declared N/kN/mN unit can be inferred from file metadata. Multiple references
or unknown force units still require user selection. Manual unit overrides
survive refresh and session restore; automatically inferred units follow their
source metadata.

Input/calibration and advanced settings are collapsed by default. Additional
coefficients default to one and are hidden until requested or non-default;
the input summary flags overrides. Without the frequency-limit checkbox,
the existing automatic frequency rules apply. Turning off a manual limit
restores zero/automatic values; turning it back on recalls the last manual
range. Non-default algorithm parameters and saved non-CMIF methods remain
visible in the compact summary, even with advanced settings collapsed.
Resetting algorithm defaults selects CMIF but does not clear calibration,
driving-point confirmation or frequency limits. Older sessions retain their
explicit choices rather than being silently reset to CMIF.
