# Container dependency policy

The CUDA and PyTorch files are wheel locks: every package is installed with
`--no-deps --require-hashes`, and the hashes select the Python 3.12 x86-64
wheels used by the RunPod image. They are intentionally split so no dependency
installation creates a multi-gigabyte Docker layer.

`runtime.txt` contains exact top-level pins for the application and the small
Python dependencies required by PyTorch. Its transitive dependencies are
resolved by pip during the image build. CI rejects ranges and unpinned direct
requirements. Updating it must be followed by an image build, `pip check`, the
offline import smoke test, vulnerability scanning, and the remote layer-size
gate.

Models and Hugging Face snapshots never belong in these files or in the image.
