# Unified VX + just toolchain entry point.

set shell := ["sh", "-cu"]
set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

default:
    @vx just@1.58 --list

# Verify the pinned VX and just toolchain.
toolchain-ci:
    vx just@1.58 --version