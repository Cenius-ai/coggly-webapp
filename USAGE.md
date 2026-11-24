# Using Coggly

Every command is one invocation of one process. There is no REPL, no TUI menu
and no daemon: you type a command, you get plain text back, and the data file is
written only if something changed.

Shape of the command line:

```
coggly <group> <command> [options]        groups: beat, pendulum, service
```

`coggly --help` lists the groups; `coggly beat --help` (or `pendulum`,
`service`) lists that group's commands and flags. `--data PATH` may be given
before or after the group name, so a demonstration never touches your real
history.

The transcripts below are real output from this build, produced by running the
commands in the order shown. Only the dates depend on the day you run them.

## Beat-error log — `coggly beat …`

Beat error is always **seconds per day**, positive = running fast. The accepted
window is **−600 … 600 s/day**; anything else is rejected, because a reading
beyond that is not a regulation problem but a fault.

### Record a reading

```console
$ coggly beat add --movement P-42 --error 4.5
Added reading 0001 - P-42 on 2026-11-24: 4.5 s/day (fast)

$ coggly beat add --movement P-42 --error -2.3 --date 2026-11-02 --note "cold morning in the shop"
Added reading 0002 - P-42 on 2026-11-02: -2.3 s/day (slow) - note: cold morning in the shop

$ coggly beat add --movement W-08 --error 3.3 --note "carriage clock, just cleaned"
Added reading 0003 - W-08 on 2026-11-24: 3.3 s/day (fast) - note: carriage clock, just cleaned
```

* `--movement` — required. Any label you like: `P-42`, `K-17`, `grandmother-1898`.
* `--error` — required, a number in s/day, kept to 0.1 s/day.
* `--date` — optional, `YYYY-MM-DD`; defaults to today.
* `--note` — optional; it appears in the list when the row still fits 80 columns.

The confirmation repeats the id, movement, date, rate and direction, so you can
copy the id straight into `beat remove`.

### List readings

```console
$ coggly beat list
ID    DATE        MOVEMENT  S/DAY  NOTE
----  ----------  --------  -----  ----------------------------
0003  2026-11-24  W-08        3.3  carriage clock, just cleaned
0001  2026-11-24  P-42        4.5
0002  2026-11-02  P-42       -2.3  cold morning in the shop

3 readings

$ coggly beat list --movement P-42
ID    DATE        MOVEMENT  S/DAY  NOTE
----  ----------  --------  -----  ------------------------
0001  2026-11-24  P-42        4.5
0002  2026-11-02  P-42       -2.3  cold morning in the shop

2 readings for P-42
```

Newest first (by date, then by id). The footer is singular where it should be
(`1 reading`), and the `NOTE` column only appears when some reading has a note.

With nothing recorded, Coggly names the exact next command instead of printing
an empty screen:

```console
$ coggly beat list
No readings yet - add one with: coggly beat add --movement P-42 --error 3.2
```

### Summarise a movement

```console
$ coggly beat summary --movement P-42
Movement      P-42
Readings      2
Latest        4.5 s/day (fast) on 2026-11-24
Mean          1.1 s/day
Min           -2.3 s/day
Max           4.5 s/day
Last service  no service records for this movement
```

Leave `--movement` off to get one block per movement — the quickest way to see
which pieces in the shop are furthest from rate. The `Last service` line fills
in as soon as the movement has service records.

### Remove a reading

```console
$ coggly beat remove 0002
Removed reading 0002 - P-42 on 2026-11-02: -2.3 s/day (slow)

$ coggly beat remove 9999; echo "exit $?"
error: no reading has that id (9999)
hint: run: coggly beat list
exit 1
```

The confirmation names exactly what was removed (id, movement, date, rate), and
a rejected id leaves every other reading untouched. Ids are short zero-padded
counters and are never reused, so a printed id keeps pointing at the same
reading.

Rejected input is one line on stderr, exit 1, nothing stored:

```console
$ coggly beat add --movement P-42 --error abc
error: the beat error must be a number between -600 and 600 seconds per day (got 'abc')

$ coggly beat add --movement P-42 --error 601
error: the beat error must be between -600 and 600 seconds per day (got 601)

$ coggly beat add --error 3
error: a movement number is required - e.g. --movement P-42

$ coggly beat add --movement P-42 --error 3 --date 2026-02-30
error: the date must be a real date in YYYY-MM-DD form (got '2026-02-30')
```

## Pendulum correction — `coggly pendulum correct`

```console
$ coggly pendulum correct --length 1000 --error 10
Pendulum correction
  current length    1000.00 mm (39.370 in)
  rate error        10.0 s/day (fast)
  change            shorten by 0.23 mm (0.009 in)
  corrected length  999.77 mm (39.361 in)
  regulating nut    turn it DOWN to lengthen, UP to shorten the pendulum
```

* `--length` — the pendulum's effective length in millimetres; must be greater
  than zero.
* `--error` — the measured error in seconds per day, same sign convention as the
  log (positive = fast).

Every run states the direction as a verb (`shorten` / `lengthen`), gives the
change in millimetres with the inch equivalent in parentheses, reports the
corrected length the same way, and says which way the regulating nut goes.
Running the estimator is free: it reads and writes nothing.

Sign cases:

| Error | Direction | Reason |
| --- | --- | --- |
| `+10` (fast) | shorten | `dL = −2 · L · (error / 86400)` comes out negative |
| `−10` (slow) | lengthen | the same formula comes out positive |
| `0` | none — the clock is in rate, with a `0.00 mm (0.000 in)` change | there is nothing to correct |

```console
$ coggly pendulum correct --length 1000 --error -10
Pendulum correction
  current length    1000.00 mm (39.370 in)
  rate error        -10.0 s/day (slow)
  change            lengthen by 0.23 mm (0.009 in)
  corrected length  1000.23 mm (39.379 in)
  regulating nut    turn it DOWN to lengthen, UP to shorten the pendulum

$ coggly pendulum correct --length 1000 --error 0
Pendulum correction
  current length    1000.00 mm (39.370 in)
  rate error        0.0 s/day (in rate)
  change            none, the clock is in rate: 0.00 mm (0.000 in)
  corrected length  1000.00 mm (39.370 in)
  regulating nut    turn it DOWN to lengthen, UP to shorten the pendulum
```

Rejections (one line on stderr, exit 1, nothing calculated):

```console
$ coggly pendulum correct --length 0 --error 5
error: the pendulum length must be greater than zero millimetres (got 0)

$ coggly pendulum correct --length 1000 --error fast
error: the beat error must be a number between -600 and 600 seconds per day (got 'fast')

$ coggly pendulum correct
usage: coggly pendulum correct --length MM --error S_PER_DAY
example: coggly pendulum correct --length 1000 --error 10
         -> shorten by 0.23 mm (0.009 in); corrected length 999.77 mm (39.361 in)
```

The estimator is pendulum-only. Balance-wheel and hairspring rate correction is
different physics and is out of scope, as are temperature, barometric and
amplitude compensation.

## Service history — `coggly service …`

### Record a service

```console
$ coggly service add --movement P-42 --date 2025-02-22 \
      --work "full service, clean and oil" --watchmaker "A. Bell" --cost 145
Added service record 0001 - P-42 on 2025-02-22: full service, clean and oil (A. Bell) - cost 145.00

$ coggly service add --movement P-42 --date 2026-09-25 \
      --work "mainspring replaced" --watchmaker "A. Bell" --cost 68.5 \
      --note "old spring 12% down on torque"
Added service record 0002 - P-42 on 2026-09-25: mainspring replaced (A. Bell) - cost 68.50 - note: old spring 12% down on torque

$ coggly service add --movement W-08 --date 2026-11-06 \
      --work "clean, oil, new suspension spring" --watchmaker "M. Okafor" --cost 82
Added service record 0003 - W-08 on 2026-11-06: clean, oil, new suspension spring (M. Okafor) - cost 82.00
```

* `--movement`, `--work`, `--watchmaker` — required.
* `--date` — optional `YYYY-MM-DD`, defaults to today.
* `--cost`, `--note` — optional.

A missing movement number, an impossible date or a negative cost is one line and
an exit code of 1:

```console
$ coggly service add --work "oil"
error: a movement number is required - e.g. --movement P-42

$ coggly service add --movement P-42 --date 2026-13-40 --work oil
error: the date must be a real date in YYYY-MM-DD form (got '2026-13-40')
```

### One movement's history

```console
$ coggly service list --movement P-42
ID    DATE        ELAPSED   WORK                         WATCHMAKER  COST    NOTE
----  ----------  --------  ---------------------------  ----------  ------  ------
0001  2025-02-22  640 days  full service, clean and oil  A. Bell     145.00
0002  2026-09-25  60 days   mainspring replaced          A. Bell     68.50   old ..

Latest service 2026-09-25 (60 days ago) - 2 records for P-42
```

Oldest first, with whole-day elapsed counts, then the latest service. The
`COST` column appears only when some record has a cost, `NOTE` only when some
record has a note, and a note too long for the remaining width is abbreviated
with `..` rather than wrapping the row.

A movement with no records gets the invitation rather than a blank table:

```console
$ coggly service list --movement ZZZ
No service records for ZZZ - add one with: coggly service add --movement ZZZ --work "full service"
```

### Every movement that has records

```console
$ coggly service movements
MOVEMENT  RECORDS  LAST SERVICE  DAYS SINCE
--------  -------  ------------  ----------
P-42            2  2026-09-25    60 days
W-08            1  2026-11-06    18 days

2 movements, 3 records
```

With nothing recorded at all:

```console
$ coggly service movements
No service records yet - add one with: coggly service add --movement P-42 --work "full service" --watchmaker "A. Bell"
```

## Scripting and piping

Results are stable plain text on stdout, errors are on stderr, and the exit codes
are `0` success / `1` user or data error / `2` unknown command. That is enough to
drive Coggly from a shell or a Makefile:

```console
$ coggly beat list | grep '  P-42  '              # only P-42 rows
$ coggly beat summary --movement P-42 | grep Max  # one number out of the block
$ coggly beat add --movement K-17 --error -8 || echo "reading rejected"
$ test -f ~/.coggly.json && echo "history present"
```

The JSON store is itself the machine-readable form — `schema_version` plus two
arrays with fixed field names — so `jq` or a five-line Python script can read it
directly. Always write through Coggly (`beat add`, `beat remove`, `service add`)
rather than editing the file: that is what keeps the write atomic and the ids
consistent.

## Where the data lives

`~/.coggly.json` by default. `--data PATH` points at another file — a second
bench, a scratch experiment, a test:

```console
$ cp examples/bench-demo.json /tmp/bench.json
$ coggly --data /tmp/bench.json beat list
$ coggly beat list --data /tmp/bench.json      # the flag works in both positions
```

The file is created on first write, is written atomically (temp file plus
`os.replace`), and is never overwritten if it cannot be read — you get its path
and a one-line recovery hint instead:

```console
$ coggly beat list
error: the data file /home/u/.coggly.json is not valid JSON (Expecting property name enclosed in double quotes at line 1)
hint: fix it or move it aside (mv /home/u/.coggly.json /home/u/.coggly.json.bak) - Coggly will not overwrite it
```
