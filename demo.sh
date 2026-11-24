#!/usr/bin/env bash
# Coggly end-to-end demo.
#
# Seeds a throwaway data file through the real command line and walks every
# group: accounts (register, login, logout), the beat-error log, the pendulum
# calculator and the service history. Non-interactive, exits 0, never touches
# ~/.coggly.json. Run it with:  bash demo.sh
set -euo pipefail
cd "$(dirname "$0")"

DEMO_DATA="$(mktemp -t coggly-demo-XXXXXX)"
rm -f "$DEMO_DATA"                 # let Coggly create the file on first write
trap 'rm -f "$DEMO_DATA"' EXIT

# A date N days before today, so the seeded bench always looks current.
day() { python3 -c "from datetime import date, timedelta; print((date.today() - timedelta(days=$1)).isoformat())"; }

run() {
    echo
    echo "\$ coggly $*"
    python3 coggly.py --data "$DEMO_DATA" "$@"
}

# For the deliberately rejected commands: show the message and keep going.
run_soft() {
    echo
    echo "\$ coggly $*"
    python3 coggly.py --data "$DEMO_DATA" "$@" || echo "(exit $?)"
}

echo "Coggly demo - data file: $DEMO_DATA (a copy of ~/.coggly.json, thrown away at the end)"
echo "================================================================================"

run --help
run auth --help

echo
echo "-- signing in (the seeded demo watchmaker) -------------------------------"
run auth login --email cenius@cenius.ai --password cenius
run auth whoami

echo
echo "-- the beat-error log ----------------------------------------------------"
run_soft beat list
run beat add --movement P-42 --error 6.4 --date "$(day 28)" --note "after mainspring replacement"
run beat add --movement P-42 --error 3.1 --date "$(day 14)"
run beat add --movement P-42 --error 1.4 --date "$(day 2)" --note "second turn on the regulating nut"
run beat add --movement K-17 --error -12.5 --date "$(day 9)" --note "long-case clock, cold workshop"
run beat list
run beat list --movement P-42
run beat summary --movement P-42
run_soft beat add --movement P-42 --error abc

echo
echo "-- the pendulum calculator (needs no account) ----------------------------"
run pendulum correct --length 1000 --error 10
run pendulum correct --length 1000 --error -10
run pendulum correct --length 1000 --error 0

echo
echo "-- service history -------------------------------------------------------"
run service add --movement P-42 --date "$(day 410)" --work "full service, clean and oil" --watchmaker "A. Bell" --cost 145
run service add --movement P-42 --date "$(day 26)" --work "mainspring replaced" --watchmaker "A. Bell" --cost 68.5 --note "old spring 12% down on torque"
run service add --movement K-17 --date "$(day 190)" --work "oil and regulate" --watchmaker "M. Okafor"
run service list --movement P-42
run service movements

echo
echo "-- a second watchmaker, and what they can see ----------------------------"
printf 'anvil-2024\n' | python3 coggly.py --data "$DEMO_DATA" auth register --email m.okafor@example.com --name "M. Okafor" --username okafor --password-stdin
run auth whoami
run beat list
run service movements

echo
echo "-- signing out revokes the session ---------------------------------------"
run auth logout
run_soft auth whoami
run_soft beat list

echo
echo "-- back to the first watchmaker ------------------------------------------"
run auth login --email cenius --password cenius
run beat remove 0002
run beat list

echo
echo "\$ cat \$DEMO_DATA"
cat "$DEMO_DATA"
echo
echo "Demo complete - accounts, readings and service records all live in ONE JSON file."
