# Making OPUS work in 2023

by Samuel Grayson

According to [ProvMark], OPUS is one of the state-of-the-art provenance collection systems.
In particular, it is the _only_ provenance system studied by ProvMark which does not require root access to the targetted system.
However, OPUS is quite old, so building it is difficult.
Even after I surmounted that difficulty, it seems that OPUS does not work.

The original OPUS software was developed for a [2013 USENIX publication].
The authors made sporadic commits up to 2016 (see [commit history]), but the software mostly fell into disrepair.
However, it was used as recently as 2019 by the authors of [ProvMark].
While ProvMark is open-source, their [README file for OPUS] only says,

> As OPUS is not published anywhere in the internet, it is not able to generate a vargrant file that completely install opus within. The attached vargrant file is just for building the environment for opus and prov mark. You will need to obtain your own copy of OPUS and extracted within the vagrant VM in order to use OPUS with Prov Mark system.

As such, we do not know exactly how the ProvMark authors installed or ran OPUS.
This note is quite wrong; OPUS is available on GitHub since at least 2016.

So I set out building it myself.
Long-story-short, I've put in a lot of hard work so that you can just install nix, and run:

```
cd ./nix/
nix build
./result/bin/opusctl.py config
./result/bin/opusctl.py server start
```

## More details

I used the [Nix package manager] to create an old environment.
Nix allows us to build a local software environment that neither pollutes nor is polluted by the system environment (like virtualenvs, but for arbitrary programs).
Nix also has helpers for building software in a sandboxed, deterministic way.
The environment I developed resides in a Nix flake located in [`./nix/flake.nix`].

The first challenge is just building OPUS, which uses a bespoke recurisve Make.
Some stages of the build use a protobuf compiler or Python script to generate source code for other stages of the build.
There is a back-end and front-end; see the [OPUS publication] for architecture details.
The back-end is entirely one file: `libopusinterpose.so` which is intended to be specified in `LD_PRELOAD`; `libopusinterpose.so` interposes standard libc calls such as, `open(...)`.
The front-end is a set of Python scripts that reference a Python library called `opus`.
These scripts are documented below in this `REAMDE.md` file.
`libopusinterpose.so` uses environment variables to find the UNIX socket of the front-end server.
The front-end server listens to and stores data sent on this socket.

From the `./nix/` directory, `nix build .#opus-backend` will build the `./libopusinterpose.so` and files needed for the frontend.
These will be placed in the nix store `/nix/store/something` (absolute path) and symlinked to a directory called `./result` (relative path).
These just require careful setting of environment variables, a protobuf compiler, protobuf libraries, and openssl libraries.

`nix build .#opus-frontend` will build the frontend scripts (and `.#opus-backend`, if not already built) in a directory called `./result/bin`.
OPUS requires Python 2.7; while it wouldn't be too hard to update OPUS to Python 3 (see `./python3.patch`), it would be quite difficult to update all of its dependencies to Python 3, while preserving their then-current API.
This is much more hairy; it needs Python 2.7 {neo4j-embedded, pyyaml, jpype1, psutil, prettytable, protobuf, termcolor, jdk, six, protobuf, jinja2}.
All of which dropped support for Python 2.7 a long time ago.
As such, my Nix flake specifies and builds-from-source old versions of these packages.
But these came with their own problems:

- neo4j-embedded does not exist anymore in PyPI, but I found the repository on GitHub and built it form source. It depends on Neo4j 1.9 (end-of-life 2014) because of [this line][neo4j-1.9]. Additionally, neo4j-embedded requires a maven step and some source-code modifications which I painstakingly applied in `neo4j-pom.patch` and `neo4j-src.patch`.
- Newer versions of Jpype1 did not work with this code, so I had to use exactly version 0.5.4.3, which was tested against JDK 1.6 (end-of-life 2015). JDK1.6 does not exist in Nixpkgs, and building from source is arduous, but JDK8 does exist in Nixpkgs and seems to work sufficiently well.
- The version of Protobuf must be synchronized with the version used to build the backend. Protobuf 3.17.3 is the latest which nominally supports Python 2.7. However, this version uses namespace packages which must be reduced to normal packages before it will work with Python 2.7 (see `protobuf-python-bindings.patch`).

In the interest of reproducibility, the Nix flake use a specific version of GLibC rather than your system's version to compile against.
You could tell it to use your system's version, or muck with the LD_LIBRARY_PATH so that the loader uses your system's version, or build the application you want to run with Nix.
I think the last approach is the easiest.
The environment `.#opus-env` contains Busybox, the backend, and the frontend all built with the same toolchain.
It can be built with `nix build .#opus-env`.
Use these binaries instead of your system binaries when launching a process, for example:

```
./result/bin/opusctl.py process launch ./result/bin/sh -c './result/bin/ls > data/file0'
```

[2013 USENIX publication]: https://www.usenix.org/conference/tapp13/technical-sessions/presentation/balakrishnan
[OPUS publication]: https://www.usenix.org/conference/tapp13/technical-sessions/presentation/balakrishnan
[commit history]: https://github.com/DTG-FRESCO/opus/commits/master
[ProvMark publication]: https://dl.acm.org/doi/10.1145/3361525.3361552
[README file for OPUS]: https://github.com/arthurscchan/ProvMark/tree/master/vagrant/opus
[Nix package manager]: https://nixos.org
[`./nix/flake.nix`]: https://github.com/charmoniumQ/opus/blob/master/nix/flake.nix
[neo4j-1.9]: https://github.com/neo4j-contrib/python-embedded/blob/a76def699d60a5ec1c27f81ca8f7744f11132162/pom.xml#L37

# OPUS
## Introduction
OPUS is a system for tracking the effects programs have on your system and improving the productivity of your work. It captures the effects programs have using LD_PRELOAD based interposition and stitches this data together into a graph of all the interactions on the system. Then it provides a set of tools that let you query this graph for information.

## Installation
1. Download the latest release.
1. Extract the archive to the chosen install location.
1. Open a terminal inside the extracted location.
1. ./update_wrapper
1. bin/opusctl conf -i
1. cat /tmp/install-opus >> ~/.bashrc
1. Open a fresh shell.

## OPUS Control script
Various common tasks you may want to perform with the different parts of the system.

### Configuration
* `opusctl conf <$flag>`
  * Allows you to configure the OPUS environment.

### Server
* `opusctl server start`
  * Starts the provenance collection server.
* `opusctl server stop`
  * Stops the provenance collection server.
* `opusctl server ps`
  * Lists all processes currently being interposed.
* `opusctl server status`
  * Returns the status of each component in the provenance server.
  * Returns the count of number of messages in the provenance server queue.
* `opusctl server detach $pid`
  * Closes connection with the process identifier $pid.
* `opusctl server getan`
  * Returns the current provenance analyser type.
* `opusctl server setan $new_an`
  * Sets the provenance analyser to $new_an.

### Frontend
* `opusctl process launch <$prog>`
  * Launches $prog under OPUS if supplied, else launches a new shell session under OPUS.
* `opusctl process exclude $prog`
  * While in a shell which is under OPUS interposition this launches `$prog` free from interposition.

### Utilities
* `opusctl util ps-line`
  * Returns a colored indicator that tells you the backend and frontend status combination.


### Query Tools
* `env_diff_client $prog`
  * Compares the environment and other process context between two executions of $prog.

* `last_cmd`
  * Returns the last N commands executed on a file or from a specific directory.

* `gen_epsrc $file`
  * Generates a report and archive package on $file for the EPSRC open data compliance.

* `gen_script $file`
  * Generates a script that canonicalises the workflow used to produce $file.

* `gen_tree $file`
  * Renders a tree representation of the workflow used to produce $file.

## Trouble Shooting

Run `./opusctl.py config` and set `debug_mode` to `True`.

## Helpful Links
* http://www.cl.cam.ac.uk/research/dtg/fresco/
* https://www.cl.cam.ac.uk/research/dtg/fresco/opus/
* https://github.com/DTG-FRESCO/opus
