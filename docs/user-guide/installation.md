# Installation

## Requirements

- Python 3.11 or newer
- OBS Studio 28+ (for the overlay; optional)
- Windows, macOS or Linux

## Install

From a checkout of the repository:

```sh
python -m venv .venv
# Linux/macOS:
. .venv/bin/activate
# Windows (PowerShell):
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

Check that it works:

```sh
estim-camming --version
estim-camming plugins
```

`estim-camming plugins` lists the supported streaming platforms, devices and
patterns, with their options.

## Optional extras

| Command | Adds |
|---|---|
| `pip install -r requirements-dev.txt` | pytest, ruff and Sphinx, for development and building this documentation |
| `pip install -e '.[estim2b]'` | The E-Stim 2B device, see [E-Stim 2B setup](estim2b.md) |
| `pip install -e '.[gui]'` | The desktop control window (`estim-camming gui`), see [Control window](gui.md) |

Device and platform plugins from other packages are installed with `pip` as
well. They show up in `estim-camming plugins` automatically.
