# Modal Sessions

The Modal Shape control tab provides Export Modal Session and Import Modal
Session buttons. A .vnamodal file is a self-contained ZIP container with a
versioned JSON manifest and NumPy array entries; it does not use pickle.

The session includes datasets (including complex FRFs and time data), point
mapping rows, enabled flags, coefficients, connection rows, automatic/manual
frequency candidates, the current extracted mode, display gain, animation
frame count, preview phase, and both 3D cameras. Continuous datasets are
materialized on export so reopening does not require the original files.

Import replaces the current modal session and its corresponding shared
datasets after confirmation. Other shared datasets are retained. The restored
animation is paused; use Animation Preview to run it. GIF files are not embedded
because they can be regenerated from the restored mode and settings.

Unsupported formats and archives larger than 2 GiB uncompressed are rejected.
Export uses an atomic replacement, so a failed save does not overwrite a prior
session file. The product tests cover restoration after removal of source VNA
files, complex results, GIF regeneration, and shared dataset synchronization.
