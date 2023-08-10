{
  inputs = {
    flake-utils = {
      url = "github:numtide/flake-utils";
    };
    mvn2nix = {
      url = "github:fzakaria/mvn2nix";
    };
  };

  outputs = { self, nixpkgs, flake-utils, mvn2nix }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs {
          system = system;
          config = {
            permittedInsecurePackages = [
                "python-2.7.18.6"
                "python-2.7.18.6-env"
            ];
          };
        };
        python = pkgs.python27;
        version = "0.1.0";
      in rec {
        packages = rec {
          termcolor = python.pkgs.buildPythonPackage rec {
            pname = "termcolor";
            version = "1.0.1";
            src = pkgs.fetchFromGitHub {
              owner = "termcolor";
              repo = "${pname}";
              rev = "${version}";
                hash = "sha256-t9ILHypTyZOOApXE5seeoJaJwkWnBu8g3uMH+wX2Xq4=";
            };
            pythonImportsCheck = [
              "termcolor"
            ];
          };
          pyyaml = python.pkgs.buildPythonPackage rec {
            pname = "pyyaml";
            version = "5.4.1.1";
            src = pkgs.fetchFromGitHub {
              owner = "yaml";
              repo = "${pname}";
              rev = "${version}";
              hash = "sha256-qLdAMqoyEXRIqcNuHBBtST8GWh5gmx5fBU/q3f4zaOw=";
            };
            nativeBuildInputs = [ python.pkgs.cython python.pkgs.pathlib ];
            buildInputs = [ pkgs.libyaml ];
            checkPhase = ''
              runHook preCheck
              PYTHONPATH="tests/lib:$PYTHONPATH" ${python.interpreter} -m test_all
              runHook postCheck
            '';
            pythonImportsCheck = [ "yaml" ];
          };
          prettytable = python.pkgs.buildPythonPackage rec {
            pname = "prettytable";
            version = "1.0.1";
            src = pkgs.fetchFromGitHub {
              owner = "jazzband";
              repo = "${pname}";
              rev = "refs/tags/${version}";
              hash = "sha256-B7GbAueBWWm5k3Y0pmOir3mIoMy/L5+4sQ0rG3797ck=";
            };
            SETUPTOOLS_SCM_PRETEND_VERSION = version;
            propagatedBuildInputs = [
              python.pkgs.wcwidth
              python.pkgs.setuptools-scm
            ];
            nativeCheckInputs = [
              python.pkgs.pytest-lazy-fixture
              python.pkgs.pytestCheckHook
            ];
            pythonImportsCheck = [
              "prettytable"
            ];
          };
          ipaddress = python.pkgs.buildPythonPackage rec {
            pname = "ipaddress";
            version = "1.0.23";
            src = pkgs.fetchFromGitHub {
              owner = "phihag";
              repo = "${pname}";
              rev = "v${version}";
              hash = "sha256-SFgXcXigIyQ5ZLvZ5h1zzNoCEOqdvg8usd1XOr6zdLM=";
            };
            doCheck = true;
            pythonImportsCheck = [
              "ipaddress"
            ];
          };
          psutil = python.pkgs.buildPythonPackage rec {
            pname = "psutil";
            version = "5.9.5";
            format = "setuptools";
            src = pkgs.fetchFromGitHub {
              owner = "giampaolo";
              repo = "psutil";
              rev = "release-5.9.5";
              hash = "sha256-aXiv4U1AeVnTWEdeu9RFUEzOL82yH69qMgKRKU7mmv8=";
            };
            buildInputs =
              # workaround for https://github.com/NixOS/nixpkgs/issues/146760
              pkgs.lib.optionals (pkgs.stdenv.isDarwin && pkgs.stdenv.isx86_64) [
                pkgs.CoreFoundation
              ] ++ pkgs.lib.optionals pkgs.stdenv.isDarwin [
                pkgs.IOKit
              ];
            nativeCheckInputs = [
              python.pkgs.pytestCheckHook
              python.pkgs.mock
              ipaddress
            ];
            # Segfaults on darwin:
            # https://github.com/giampaolo/psutil/issues/1715
            doCheck = !pkgs.stdenv.isDarwin;
            # In addition to the issues listed above there are some that occure due to
            # our sandboxing which we can work around by disabling some tests:
            # - cpu_times was flaky on darwin
            # - the other disabled tests are likely due to sanboxing (missing specific errors)
            pytestFlagsArray = [
              # Note: $out must be referenced as test import paths are relative
              "$out/${python.sitePackages}/psutil/tests/test_system.py"
            ];
            disabledTests = [
              # Some of the tests have build-system hardware-based impurities (like
              # reading temperature sensor values).  Disable them to avoid the failures
              # that sometimes result.
              "cpu_freq"
              "cpu_times"
              "disk_io_counters"
              "sensors_battery"
              "sensors_temperatures"
              "user"
              "test_disk_partitions" # problematic on Hydra's Linux builders, apparently
            ];
            pythonImportsCheck = [
              "psutil"
            ];
          };
          protobuf = pkgs.stdenv.mkDerivation rec {
            # This file exists, but I can't figure out how to call it
            # https://github.com/NixOS/nixpkgs/blob/nixos-23.05/pkgs/development/libraries/protobuf/3.17.nix
            pname = "protobuf";
            version = "3.17.3";
            src = pkgs.fetchFromGitHub {
              owner = "protocolbuffers";
              repo = "protobuf";
              rev = "v${version}";
              sha256 = "08644kaxhpjs38q5q4fp01yr0wakg1ijha4g3lzp2ifg7y3c465d";
            };
            postPatch = ''
              rm -rf gmock
              cp -r ${pkgs.gtest.src}/googlemock gmock
              cp -r ${pkgs.gtest.src}/googletest googletest
              chmod -R a+w gmock
              chmod -R a+w googletest
              ln -s ../googletest gmock/gtest
            '' + pkgs.lib.optionalString pkgs.stdenv.isDarwin ''
              substituteInPlace src/google/protobuf/testing/googletest.cc \
                --replace 'tmpnam(b)' '"'$TMPDIR'/foo"'
            '';
            nativeBuildInputs = [ pkgs.autoreconfHook pkgs.buildPackages.which pkgs.buildPackages.stdenv.cc ];
            buildInputs = [ pkgs.zlib ];
            enableParallelBuilding = true;
            doCheck = true;
            dontDisableStatic = true;
          };
          protobuf-python-bindings = python.pkgs.protobuf.overrideAttrs (newAttrs: oldAttrs: {
            # Take my older (3.17) version of protobuf instead.
            pname = protobuf.pname;
            src = protobuf.src;
            version = protobuf.version;
            protobuf = protobuf;
            buildInputs = [ protobuf ];
            propagatedNativeBuildInputs = [ protobuf ];
            prePatch = "true";
            patches = [
              ./protobuf-python-bindings.patch
            ];
            propagatedBuildInputs = [ python.pkgs.six ] ++ (oldAttrs.propagatedBuildInputs or []);
          });
          jdk = pkgs.jdk8;
          jpype1 = python.pkgs.buildPythonPackage rec {
            # We need old version
            # due to use of __javaclass__ and "Static field 'addLabel' is not settable on Java 'org.neo4j.kernel.impl.core.NodeProxy' object" and possibly other errors.
            # https://jpype.readthedocs.io/en/latest/CHANGELOG.html
            # See also the old nixpkgs version:
            # https://github.com/NixOS/nixpkgs/blob/84cf00f98031e93f389f1eb93c4a7374a33cc0a9/pkgs/development/python-modules/JPype1/default.nix
            pname = "JPype1";
            version = "0.5.4.3";
            src = pkgs.fetchFromGitHub {
              owner = "jpype-project";
              repo = "jpype";
              rev = "${version}";
              hash = "sha256-nkaaNuczA2xzxqSOw9UMKBewytPaa2MKF7jNR+FwtQ0=";
            };
            buildInputs = [
              jdk
            ];
            propagatedBuildInputs = [
              jdk
            ];
            doCheck = true;
            # nativeCheckInputs = [
            #   python.pkgs.pytestCheckHook
            # ];
            # disabledTestPaths = [
            #   "test/buf_leak_test.py"
            #   "test/test_awt.py"
            # ];
            checkPhase = ''
              runHook preCheck
              python test/testsuite.py
              runHook postCheck
          '';
            pythonImportsCheck = [
              "jpype"
            ];
          };
          neo4j = pkgs.stdenv.mkDerivation rec {
            pname = "neo4j";
            version = "1.9";
            src = pkgs.fetchurl {
              url = "https://neo4j.com/artifact.php?name=neo4j-community-${version}-unix.tar.gz";
              hash = "sha256-gW3GW6U0Y7+Izpf/UGX13RbxuCeLsQZpIPxvaIHkFVg=";
            };
            nativeBuildInputs = [
              pkgs.makeWrapper
            ];
            installPhase = ''
              mkdir -p "$out"
              cp -R * "$out"

              mkdir -p "$out/bin"
              for NEO4J_SCRIPT in neo4j neo4j-shell
              do
                  chmod +x "$out/bin/$NEO4J_SCRIPT"
                  wrapProgram "$out/bin/$NEO4J_SCRIPT" \
                      --prefix PATH : "${pkgs.lib.makeBinPath [ jdk pkgs.which pkgs.gawk ]}" \
                      --set JAVA_HOME "${jdk}"
              done

              # Putting jars in $out/share/java ensures they get placed on the CLASSPATH
              # https://ryantm.github.io/nixpkgs/languages-frameworks/java/#sec-language-java
              mkdir -p $out/share/java
              cp $out/lib/*.jar $out/share/java
          '';
          };
          neo4j-embedded-jars = pkgs.stdenv.mkDerivation {
            pname = "neo4j-embedded-jars";
            version = "1.9";
            src = pkgs.fetchFromGitHub {
              owner = "neo4j-contrib";
              repo = "python-embedded";
              rev = "a76def699d60a5ec1c27f81ca8f7744f11132162";
              hash = "sha256-k/hsmWN3TQDJCo99HwQA/vW94Z43pRUEkSrcJmBUUrg=";
            };
            nativeBuildInputs = [
              jdk
              pkgs.maven
            ];
            patches = [
              ./neo4j-pom.patch
              ./neo4j-src.patch
            ];
            buildPhase = ''
              mvn package --offline -Dmaven.repo.local=${mvn2nix.legacyPackages.${system}.buildMavenRepositoryFromLockFile { file = ./mvn2nix-lock.json; }}
              mkdir -p $out/share/java
              mv target/* $out
              mv $out/*.jar $out/share/java
            '';
            installPhase = "true";
          };
          neo4j-embedded = python.pkgs.buildPythonPackage {
            pname = "neo4j-embedded";
            version = "1.9";
            buildInputs = [
              neo4j-embedded-jars
              neo4j
            ];
            nativeBuildInputs = [
              pkgs.unzip
              jpype1
              jdk
            ];
            unpackPhase = ''
              cp ${neo4j-embedded-jars}/neo4j-python-embedded-1.9-SNAPSHOT-python-dist.zip .
              unzip neo4j-python-embedded-1.9-SNAPSHOT-python-dist.zip
              cd neo4j-embedded
            '';
            doCheck = true;
            checkPhase = ''
              runHook preCheck
              ${python.interpreter} tests/unit_tests.py
              runHook postCheck
            '';
          };
          opus-lib = pkgs.stdenv.mkDerivation {
            pname = "opus-lib";
            version = "0.1.0";
            src = builtins.path {
              path = ./..;
              name = "source";
              filter = path: type: let bname = builtins.baseNameOf path; in bname != ".git" && bname != "flake.nix";
            };
            buildInputs = [
              pkgs.openssl
              protobuf
              pkgs.which
              (python.withPackages (pypkgs: [
                pyyaml
                pypkgs.jinja2
              ]))
            ];
            buildPhase = ''
              export PYTHON="$(which python)"
              export VERSION="${version}"
              export CFLAGS2="-I${protobuf}/include -I${pkgs.openssl.dev}/include"
              export LFLAGS="-L${protobuf}/lib -L${pkgs.openssl.out}/lib"
              export PROJ_HOME=$(pwd)
              export TOP=$PROJ_HOME
              export PROJ_INCLUDE=$PROJ_HOME/include
              export OPUS_LIB_NAME=opusinterpose

              cp -r $src .
              make -C src/protobuf
              make -C src/messaging
              make -C src/frontend

              mkdir $out
              cp src/frontend/interposelib/libopusinterpose.so $out
              # This is stuff opus-py needs
              cp -r src/backend/proto_cpp_src $out
              cp src/backend/opus/{uds_msg_pb2,messaging}.py $out
              cp -r include $out
            '';
            installPhase = "true";
            pythonImportsCheck = [ "opus" ];
          };
          opus-py = python.pkgs.buildPythonPackage {
            pname = "opus";
            version = version;
            src = builtins.path {
              path = ./..;
              name = "source";
              filter = path: type: let bname = builtins.baseNameOf path; in bname != ".git" && bname != "flake.nix";
            };
            buildInputs = [
              protobuf
              pkgs.makeWrapper
            ];
            propagatedBuildInputs = [
              # TODOO: Do I need to list the packages of this Python?
              python.pkgs.jinja2
              neo4j-embedded
              pyyaml
              jpype1
              psutil
              prettytable
              protobuf-python-bindings
              termcolor
              jdk
              python.pkgs.six
              protobuf
            ];
            patches = [ ./nix.diff ];
            configurePhase = ''
              # TODO: consider removing src/backend/{proto_cpp_src,ext_src} from this copy, the above package, the source tree, and setup.py
              cp -r ${opus-lib}/proto_cpp_src ./src/backend/
              cp    ${opus-lib}/{messaging,uds_msg_pb2}.py ./src/backend/opus/
              export PROJ_INCLUDE="${opus-lib}/include";
              export OPUS_INSTALL_DIR=$PWD
              # TODO: avoid doing this CD
              mkdir -p $out/lib
              cp ${opus-lib}/libopusinterpose.so $out/lib/libopusinterpose.so
              cd src/backend
            '';
            postInstall = ''
              wrapProgram \
                $out/bin/opusctl.py \
                  --set LIBOPUS_PATH $out/lib/libopusinterpose.so \
                  --set LD_LIBRARY_PATH ${protobuf}/lib:$LD_LIBRARY_PATH \
                  --set JAVA_HOME ${jdk}/lib/openjdk/ \
                  --set PATH ${jdk}/lib/openjdk/bin
            '';
            VERSION = version;
            pythonImportsCheck = [ "opus" ];
            doCheck = false;
            # TODO: change this to doCheck
          };
          opus-env = pkgs.symlinkJoin {
            name = "opus-env";
            paths = [ pkgs.pkgsStatic.busybox opus-py ];
          };
          default = opus-env;
        };
        # TODO: make app
        # apps = {
        #   ${name} = flake-utils.lib.mkApp {
        #     drv = self.packages.${system}.${name};
        #   };
        #   default = self.apps.${system}.${name};
        # };
      }
    );
}
  /*
TODO:
- Use overrides to simplify the code
- Use 3.17 from Nixpkgs
- Python packages should work for any Python version?
- Exclude nix/ from ./src (but ensure safety)
  */
