# Desktop window design

The Windows window shows the same logbook as the terminal: lighthouse, time-aware sky, falling code, and the
selected theme's restrained colors. The interface keeps that visual identity while making the monitor easier
to read and use with a mouse.

- **Home and setup** keeps the animated scene above the views and settings. The scene comes from the terminal
  app and is drawn pixel for pixel; it shrinks before Open monitor, Help and Quit leave the first view. It fills
  the scene area without a strip at the right. Views show their session counts, followed by language, history
  window, notifications, theme and desktop text size. Settings save as soon as they change. Enter (or Open
  monitor) starts the monitor in the highlighted view.
- **The monitor header** adapts without a fold control. Its first line shows the brand, status counts, view and
  window, reader health and Live switch; desktop also has Home and Help. At narrow widths, counts compact to
  symbols such as `●2 ?1 ✓8 ✗3`, then non-urgent counts disappear. Asking and running counts plus the view and
  window stay visible. Reader health shortens to a dot with a tooltip. A single compact quota line follows,
  with bars, percentages and shortened renewal times; desktop tooltips and Details keep the full renewal
  detail. A question line appears only while questions are open; its count appears in the first line only when
  that question line is hidden. The terminal hides only the quota line on very short screens, without a key.
- **The monitor table** shows Status, Agent, Name, Model, Folder, Now, Last activity, Duration, Ctx, Total,
  Cmds, Files, Mode and PID. Both interfaces use the same content and order. Name and Now are capped at 60 and
  40 characters with ellipses and full text available in Details; other values remain readable. Status and Name
  stay sticky on the desktop during horizontal scrolling, whose scrollbar remains visible. The desktop header
  stays at the top during vertical scrolling.
- **The selected session** is a padded, rounded theme panel on desktop and a bordered terminal panel with a
  neutral title. At normal sizes, four rows show status/name/agent/folder; model plus effort/mode/start/duration/
  last activity; Total/context/commands and failures/files; then Now. A pending question follows and wraps to
  three lines in the terminal. At short heights the hero compacts to two rows: status, name, agent, folder and
  actions; then Now or the question. The remaining values stay in Details. The list keeps at least four visible
  rows at 800x560 with 18 px text. The desktop minimum window is 800x560.
- **Hero actions** put Stop in view at every size. Result, Log and Copy resume move into a keyboard-accessible
  `⋯` menu when they do not fit. Labels end in ellipses instead of clipping; metric values ellipsize before
  their labels. Stop always asks first, with Cancel as the default. Copy resume shows the exact command so it can
  be pasted into a terminal; copying does not reopen or answer the agent.
- **Six tabs** (Activity, Messages, Files, Log, Result and Details) keep the terminal's contents. Activity draws
  the latest 300 events and says how many earlier ones are hidden. Messages and results show light formatting;
  the Plain text switch turns it off in both interfaces. Full detail, Auto-scroll and Live only show their
  state, and scrolling up pauses Auto-scroll.
- **Footer and type**: footer groups refit after text-size changes and saved preferences. They never overflow or
  hide groups that fit; short screens keep a compact `g` maximize-state and `?` help strip. Cascadia Mono falls
  back to Consolas for the interface, code, logs and diffs; Segoe UI is used for longer prose. Text scales with
  the desktop text-size setting and Windows display scaling.

The desktop table headings are keyboard-focusable buttons that cycle ascending, descending and default order;
the third activation resets sorting. `<` and `>` step through columns in both interfaces. Desktop `Ctrl+R`
resets sorting; `r` and `F5` refresh the data. The terminal uses `Ctrl+R` to reset sorting and `r` to refresh.
Desktop also supports `w` to jump to the oldest question. In both interfaces `g` maximizes the list, hiding the
selected session and tabs/panes until restored; the footer and help show its state. In both interfaces `m` toggles Plain
text. Drag the handle below the desktop list to resize it; the saved `list_height` fraction ranges from 0.15 to
0.85. Maximizing temporarily overrides the height without changing the saved fraction. Press `?` or F1 for
desktop help.

The terminal header uses the same adaptive brand/counts/view/window line, with reader health and Live aligned
at the right. The compact quota line and optional question chips follow it, then one continuous thin rule. The
rule is still and muted while nothing runs; while sessions run a soft beam sweeps left to right, about three
seconds per pass, in the accent color or the warning color while a question is open. Reduced motion keeps the
still rule. Header chips and footer click targets never underline text or spaces.
