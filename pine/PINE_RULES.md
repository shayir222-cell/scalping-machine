# Pine Script v5/v6 — Authoring Rules

Reference cheat-sheet so future scripts compile on the first paste. TradingView's editor now defaults to Pine v6 and enforces stricter rules even on `//@version=5` scripts.

## Why the previous version of `scalp_strategy.pine` would not save

`alertcondition(condition, title, message)` requires its `message` parameter to be a **const string** — known at compile time. Our `json_buy` was built from `str.tostring(close, "#.####")` and other runtime values, which makes it a **series string**. Pine v6 rejects this with a type-qualifier error.

Fix: use `alert()` inside `if` blocks. `alert()` accepts series strings.

```pine
// WRONG in v6:
alertcondition(long_signal, title="LONG", message=json_buy)   // json_buy is series → error

// CORRECT in v6:
if long_signal
    alert(json_buy, alert.freq_once_per_bar_close)
```

## Pine v6 breaking changes vs v5 (the ones that bite)

| Rule | v5 → v6 |
|---|---|
| `int` / `float` no longer cast implicitly to `bool` | `if 1` → must be `if x != 0` or `if bool(1)` |
| `bool` values can never be `na` | `na(myBool)` errors |
| Integer division returns float | `5 / 2` was `2`, now `2.5`; use `int(5/2)` |
| `transp=` parameter removed | Use `color.new(c, 50)` instead |
| `timeframe.period` always has multiplier | `"D"` → `"1D"` |
| `strategy.entry/exit/order/cancel` lost `when=` | Wrap in `if` block instead |
| Default strategy margin is 100% (was 0%) | May need explicit `margin_long=0` for backtests |
| Strict const/simple/series qualifiers | Most common cause of "Cannot save" |

## Type qualifiers — the #1 source of save failures

Pine has three qualifier levels: `const` (literal) < `simple` (input or runtime-constant) < `series` (per-bar value).

Functions often demand a specific minimum:
- `alertcondition(message=...)` requires **const string**
- `alert(message=...)` accepts **series string** ✓
- `plot(linewidth=...)` requires **simple int**
- `input.*` returns **simple** values
- Anything built with `str.tostring(close)`, `str.format(...)`, or concatenation involving series → **series string**

**Rule of thumb:** if you concatenate any `str.tostring(<series>)` into a message, you MUST use `alert()`, not `alertcondition()`.

## `alert()` vs `alertcondition()`

| | `alert()` | `alertcondition()` |
|---|---|---|
| Placement | Inside `if` block (local scope) | Column 0 (global scope) |
| Message type | `series string` (dynamic OK) | `const string` only |
| TradingView UI: pick condition | "Any alert() function call" | Specific title from dropdown |
| Multiple events per script | One TV alert covers all | One TV alert per `alertcondition` title |
| Frequency control | `freq=` parameter | User-controlled in alert dialog |
| Strategy + `calc_on_every_tick=false` | Always once-per-bar-close regardless of `freq` | n/a |

**Default choice: `alert()`.** Cleaner, works with dynamic JSON, one TV alert slot per script.

## `request.security()` — best practice

Putting `ta.*` calls directly inside `request.security()` works but raises **CW10003** warning ("function should be called on each calculation for consistency"). Wrap in a function:

```pine
// Recommended pattern
htf_data() => [close, ta.ema(close, 20), ta.ema(close, 50)]
[c, e20, e50] = request.security(syminfo.tickerid, "60", htf_data(), lookahead=barmerge.lookahead_off)
```

**Non-repainting rule:** use `lookahead=barmerge.lookahead_off` (default). If you need `lookahead_on`, you MUST offset the expression with `[1]` to avoid future-data leakage.

## `str.format_time()` for ISO-8601 webhook timestamps

Default format: `"yyyy-MM-dd'T'HH:mm:ssZ"` — where `Z` (unquoted) is a **timezone offset like `+0000`**, NOT literal Z.

To get a literal `Z` (correct ISO-8601 zulu):

```pine
iso_time = str.format_time(timenow, "yyyy-MM-dd'T'HH:mm:ss'Z'", "UTC")
//                                                        ^^^ single-quoted = literal Z
```

Pattern letters supported: `yyyy`, `MM`, `dd`, `HH` (24h), `mm`, `ss`. Anything to be output literally (T, Z, slashes-as-text) must be inside single quotes within the format string.

## `strategy()` declaration syntax

**Always put `strategy(...)` on ONE line.** TV's editor parses multi-line `strategy()` declarations inconsistently and the failures look like cascading syntax errors.

```pine
// Reliable:
strategy("Name", overlay=true, max_bars_back=500, pyramiding=0)

// Often fails to parse, gives weird errors on lines below:
strategy(
    "Name",
    overlay=true,
    ...
)
```

## Indentation

Pine uses **4-space indentation**, never tabs, never mixed. Body of `if`/`for`/function blocks must be uniformly indented by 4 spaces relative to the keyword.

```pine
if long_signal
    alert(json_buy, alert.freq_once_per_bar_close)   // 4 spaces — correct
```

## Operator precedence (high to low)

1. `[]` history
2. unary `-`, `+`, `not`
3. `*`, `/`, `%`
4. `+`, `-`
5. `>`, `<`, `>=`, `<=`
6. `==`, `!=`
7. `and`
8. `or`
9. `?:` ternary

So `a or b ? x : y` parses as `(a or b) ? x : y` — the way most people expect.

`and`/`or` are **lazy** in v6 — RHS not evaluated if LHS short-circuits. If you have side effects on RHS, refactor.

## Common compile errors and what they mean

| Error | Cause |
|---|---|
| `Cannot use 'series int' where 'simple int' is expected` | Passed a runtime value where Pine wants a constant — see type qualifiers above |
| `Could not find function or function reference 'X'` | Wrong namespace (v5 used bare `ema()`, v6 needs `ta.ema()`) or typo |
| `Mismatched input '...'` | Most often a multi-line construct (function call, conditional) that Pine parses on one line — flatten it |
| `Undeclared identifier 'X'` | Variable used before declaration, or scope mismatch (declared inside `if` body but referenced outside) |
| `The condition of the 'if' statement must evaluate to a 'bool' value` (CE10101) | Passed `1` or `x != 0 ? 1 : 0` to `if` — wrap with `bool()` or use real boolean |

## Webhook JSON pattern (for this bot)

For `app/models.py::WebhookSignal` the bot expects:
- `token` (must match `WEBHOOK_TOKEN`)
- `symbol`, `action` ("buy" / "sell" / "close_long" / "close_short")
- `price`, `score` (int), `atr` (float), `tf_alignment` (int 0–4)
- `time` (ISO-8601 string, optional but enables stale-alert protection)

Optional extras the bot ignores: `leverage`, `tp1/tp2/tp3`, `sl`, `trail`.

Build the JSON string with explicit `str.tostring(value, "#.####")` for floats — Pine's default float-to-string can include scientific notation for tiny numbers, which the bot's Pydantic parser would reject.

## Quick validation checklist before pasting into TV

1. [ ] `//@version=5` (or 6) on line 1
2. [ ] `strategy(...)` or `indicator(...)` on ONE line
3. [ ] All `ta.*` inside `request.security` wrapped in `() =>` function
4. [ ] All `alert()` calls inside `if` blocks
5. [ ] No `alertcondition()` calls with `message=` containing runtime values
6. [ ] 4-space indentation, no tabs
7. [ ] No `transp=`, no `when=` parameters
8. [ ] `str.format_time` uses `'Z'` quoted for literal Z
9. [ ] If using `int()` cast on already-int values, drop it (works but noisy)

## Sources

- [Pine v5 → v6 migration guide](https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/)
- [Pine alerts concept](https://www.tradingview.com/pine-script-docs/concepts/alerts/)
- [request.security() docs](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/)
- [Pine error overview](https://www.tradingview.com/pine-script-docs/errors/overview/)
