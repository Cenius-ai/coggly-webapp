# Installing Coggly

Coggly is a single Python file with no third-party dependencies, so "install"
means "make sure a modern Python is on PATH" and, optionally, "put the `coggly`
command on PATH".

## Prerequisites

| Requirement | Notes |
| --- | --- |
| Python 3.9 or newer | `python3 --version`. Standard library only: no pip packages are needed to run Coggly. |
| A writable home directory | Accounts, sessions, readings and services are stored in `~/.coggly.json` (override with `--data PATH`). |
| Nothing else | No database, no server, no network, no environment variables, no config file. |

Coggly reads **no environment variables and needs no secrets.**

## Step 1 — install (optional but convenient)

From the project root:

```console
$ bash install.sh
```

The script:

1. checks that `python3` exists and is 3.9+;
2. upgrades `pip`/`setuptools`/`wheel` and installs the project in editable mode,
   which only adds the `coggly` console script (Coggly itself has no
   dependencies);
3. runs `python3 -c "import coggly"` and `python3 coggly.py --version` as a
   self-check;
4. exits — it never starts anything in the foreground.

It is idempotent: run it as often as you like. If pip or a package index is
unavailable (an offline workshop machine, for example), the script says so and
still finishes successfully, because `python3 coggly.py` needs no install at
all. To skip the console script entirely, do nothing: copy `coggly.py` to the
machine and run it.

Run scripts through their interpreter — `bash install.sh`, `bash demo.sh` — so a
lost execute bit in a tarball or a zip download cannot stop you.

## Step 2 — verify

```console
$ python3 coggly.py --version
coggly 2.0.0
$ python3 coggly.py --help
```

## Step 3 — sign in

The first time Coggly writes its store it also seeds the demo watchmaker. Sign
in with it, or create your own account:

```console
$ python3 coggly.py auth login --email cenius@cenius.ai --password cenius
Signed in as Cenius (demo account) <cenius@cenius.ai> (username cenius)
session valid until 2027-02-22T22:56:09

$ python3 coggly.py auth whoami
Signed in as   Cenius (demo account) <cenius@cenius.ai>
Username       cenius
Role           owner
Account        u0001
Session until  2027-02-22T22:56:09
```

To use your own account instead (`--password-stdin` keeps the password out of
your shell history):

```console
$ echo 'my-bench-password' | python3 coggly.py auth register --email you@example.com --password-stdin
Registered you@example.com as 'you' (u0002) - signed in
session valid until 2027-02-22T22:56:09
```

The seeded demo account is listed in the `## Demo accounts` table in
[README.md](README.md). It exists for preview and evaluation: delete the account
(or the store) before a production deploy.

## Step 4 — use it

```console
$ python3 coggly.py beat add --movement P-42 --error 4.5
$ python3 coggly.py beat list
$ python3 coggly.py pendulum correct --length 1000 --error 10
$ python3 coggly.py service add --movement P-42 --work "full service" --watchmaker "A. Bell"
$ python3 coggly.py service list --movement P-42
```

The data file `~/.coggly.json` is created on the first write; there is no
migration or seeding step to run by hand. See [README.md](README.md) for the
full worked session and [USAGE.md](USAGE.md) for each feature in detail.

## Step 5 — test

```console
$ python3 -m unittest -v
```

64 tests, no network, no fixtures to fetch; every store test writes to a
temporary file under a temporary `HOME`, so your real history is never touched.

`bash demo.sh` runs a scripted end-to-end session — including account creation,
sign-in and sign-out — against a temporary data file and throws it away
afterwards.

## Want a data file that is already full?

`examples/bench-demo.json` holds the demo account, three movements, seven
readings and four service records. Copy it first (Coggly records the session in
whichever store you point it at, and the copy costs nothing):

```console
$ cp examples/bench-demo.json /tmp/bench.json
$ python3 coggly.py --data /tmp/bench.json auth login --email cenius@cenius.ai --password cenius
$ python3 coggly.py --data /tmp/bench.json beat list
$ python3 coggly.py --data /tmp/bench.json service movements
```

## Uninstalling

* Remove the console script: `python3 -m pip uninstall coggly`.
* Remove the account and all data: `rm ~/.coggly.json` (keep a copy first — that
  file is your history).

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `python3: command not found` | Install Python 3.9+ from your platform, then re-run `bash install.sh`. |
| `error: not signed in …` | Sign in first: `coggly auth login --email you@example.com`. The demo account is `cenius@cenius.ai` / `cenius`. |
| `error: the email or password is not correct` | The message is the same whether the account exists or the password is wrong (by design). Check the email, or register a new account. |
| `error: your session expired …` | Sessions last 90 days; sign in again with the same account. |
| `error: the data file … is not valid JSON` | The store was edited by hand or truncated. Coggly refuses to overwrite it: fix the JSON, or move it aside with `mv ~/.coggly.json ~/.coggly.json.bak` and sign in again. |
| `error: cannot write the data file …` | The directory is not writable. Pass `--data PATH` pointing somewhere you own. |
| `coggly: command not found` after `install.sh` | The console script is not on PATH; use `python3 /path/to/coggly.py` instead. |
