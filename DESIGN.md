# Coggly — design source of truth

Coggly is a terminal product, so its "design system" is its output grammar and
its typography, not a stylesheet. This file is the committed direction every
surface (command output, help text, docs) follows.

## Committed direction

| Token | Value | Where it is applied |
| --- | --- | --- |
| Archetype | `cli` | the whole product: one file, invoked per command, no server, no UI chrome |
| Style family | calm-precise | short declarative lines, one fact per line, no exclamation, no decoration |
| Accent (ONE) | `oklch(0.58 0.12 35)` — hex `#b65d47` | status *words* (`fast`, `slow`, `in rate`) and the docs' headings. Terminal output itself carries no colour by design (F5), so the accent never appears as an escape sequence and never as a colour-only signal |
| Neutrals | terminal default foreground/background | the tool inherits the user's terminal palette instead of shipping one |
| Type personality | mono / mono | every surface is monospaced: aligned columns in output, code fences in the docs |
| Density | compact | no blank line between a heading and its block; one blank line between blocks |
| Shape language | monospace, no chrome, ASCII rules | `----` under every table header, two-space column gutters, no box-drawing, no bold |
| Layout | aligned terminal columns, help-first | `--help` is the entry point; tables are left-aligned text with right-aligned numerics |
| Surface / mode | terminal dark (inherited) | no background, no themes, no colour switcher — the terminal owns the surface |
| Type family | system monospace | the terminal's own font; nothing to load, nothing to download |

## Output grammar

1. A table is: header row, ASCII rule, rows, one blank line, one footer sentence.
2. The footer states the scope (`3 readings`, `2 readings for P-42`,
   `2 records for P-42`) with singular/plural correct.
3. A block (summary, pendulum correction, add confirmation) is `label` + value,
   labels padded to the longest label so the values line up.
4. Numbers: rate in s/day to 0.1; lengths to 0.01 mm and 0.001 in; costs to 0.02.
   Lengths always carry the inch equivalent in parentheses.
5. Status is a word after the number — `4.5 s/day (fast)` — never a colour and
   never a symbol.
6. Widths are capped at 80 columns: long cells are abbreviated with `..` rather
   than wrapping a row.
7. Errors are one line on stderr, prefixed `error: `, with an optional `hint: `
   line naming the exact next command. No tracebacks, no decoration.
8. Nothing on screen is decorative: every printed id, command and path is one
   the user can paste back into the shell.
