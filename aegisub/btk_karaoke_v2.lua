-- Created by Andy Charlie W. (ambilixer) in collaboration with Claude (Anthropic).
--
-- btk_karaoke_v2.lua
--
-- Aegisub automation macro, run in the manual step between timing and the
-- render. Turns a track's timed Main and BG lyric lines into the on-screen
-- karaoke display: a scrolling 7-row lyric stack with section labels,
-- countdowns over instrumental breaks and the outro, entry-cue dots, and a
-- background-vocals zone with its own separator.
--
-- Usage:
--   Copy only this file into Aegisub's automation/autoload folder (Aegisub
--   loads every .lua it finds there, recursively). Open a track's
--   _subs-timed.ass, apply the Main and BG styles, run Automation >
--   Karaoke Formatting v2 once, and save the result as _subs-final.ass.
--   Setup details are in HOWTO.md.
--
-- Reads:   the open file: dialogue lines styled Main and BG, the two styles,
--          each section's label (the Actor field of its first Main line) and
--          an optional END marker (see OUTRO_MARKER).
-- Writes:  generated events, appended to the same file. The original Main
--          and BG lines are commented out, not deleted.
--
-- Run it once, on a _subs-timed.ass only. The events it generates keep the
-- Main and BG styles, so a second run would collect them as lyrics; it
-- detects its own output and refuses. Save the result under the new name,
-- since _subs-timed.ass is the re-runnable source.
--
-- Requires karaskel.lua (ships with Aegisub). Written to Lua 5.1 / LuaJIT
-- semantics, which is what Aegisub runs.
--
-- How it works: every Main line, section label, instrumental break and the
-- outro is an ITEM in an ordered list, each with a height. Lines, breaks and
-- the outro can be ACTIVE: the active item sits in the active row, and every
-- other item is placed by the cumulative height between them. Each active
-- item k has a landing time A_k, and the stack scrolls from state k to state
-- k+1 during [A_{k+1} - SCROLL_MS, A_{k+1}].

script_name        = "Karaoke Formatting v2"
script_description = "Scrolling 7-line stack, sections, breaks, BG"
script_author      = "Andy Charlie W. (ambilixer)"
script_version     = "2.14"

include("karaskel.lua")

-- ============================================================================
-- CONFIG
-- ============================================================================

-- ── Frame / layout ──────────────────────────────────────────────────────────
-- The geometry below assumes the agreed styles: Main and BG both Consolas
-- bold, fontsize 92, ScaleX 90, outline 2. Lyric lines are drawn with
-- LYRIC_FSP letter-spacing (below), not the style's Spacing.
local X_CENTER      = 960
-- 3 sung rows above the active row, 3 upcoming below. The block sits close to
-- the vertical centre; while BG lines are on screen the bottom row fades out
-- to make room for the separator (BG_HIDE_ROWS).
local Y_ACTIVE      = 476     -- centre of the active row
local ROW_PITCH     = 114     -- centre-to-centre distance between lyric rows
local LINES_ABOVE   = 3       -- rows visible above the active row
local LINES_BELOW   = 3       -- rows visible below the active row

-- ── Section headers (from the Actor field of a section's first Main line) ───
local SECTION_GAP   = 80      -- height of the section item, px
local SECTION_FS    = 51      -- label fontsize
local SECTION_ALPHA = 0x70    -- label transparency (0 opaque .. 255 invisible)
local SECTION_ITALIC = true
local SECTION_UPPER = true
-- Optional: a section label pauses in the active row between the previous
-- line's end and the next line's landing, when there is room for it and the
-- section is not preceded by a break. Off by default: the stack goes
-- straight to the section's first line.
local SECTION_PAUSE       = false
local SECTION_HOLD_MS     = 300    -- previous line end -> section scrolls in
local SECTION_ACTIVE_ALPHA = 0x30  -- label transparency while in the active row

-- ── Colour hierarchy: transparency by row distance from the active row ──────
-- Index 1 = adjacent row. Rows above use the primary colour (sung); rows
-- below use the secondary colour (unsung). Longer tables than LINES_ABOVE /
-- LINES_BELOW are harmless; shorter ones clamp to the last entry.
local ALPHA_ABOVE   = { 0x50, 0x90, 0xC8 }
local ALPHA_BELOW   = { 0x18, 0x50, 0x98 }

-- ── Scroll ──────────────────────────────────────────────────────────────────
local SCROLL_MS     = 350     -- duration of one stack transition
local EASE_SEGMENTS = 4       -- 1 = linear \move; >1 = quadratic ease-out sampled in N events
local LEAD_MS       = 400     -- incoming line lands this long before its first syllable
local MIN_STATE_MS  = 60      -- floor on a state's static window (landing times forced apart)

-- ── Entry cue (dots) ────────────────────────────────────────────────────────
local CUE_GAP_MIN   = 2500    -- ms; gaps at least this long get the dots (and a longer lead)
local CUE_MS        = 2000    -- ms; dots run for this long before the onset; also the lead in that case
local DOT_COUNT     = 3
local DOT_SIZE      = 16      -- px square
local DOT_GAP       = 16      -- px between dots
local DOT_MARGIN    = 53      -- px from the last dot to the line's left edge

-- ── Breaks ──────────────────────────────────────────────────────────────────
local BREAK_THRESHOLD = 10000 -- ms; a gap at least this long becomes a break item
local BREAK_HOLD_MS   = 600   -- the finished line holds the active row this long before the break scrolls in
local BREAK_MARKER_FS = 51    -- upcoming / past marker fontsize
local BREAK_MARKER_ALPHA = 0x60
local BREAK_TIMER_FS  = 74    -- countdown fontsize while the break is active
local BREAK_TIMER_DY  = -13   -- countdown text centre relative to the row centre
local BAR_WIDTH       = 1104
local BAR_HEIGHT      = 12
local BAR_DY          = 40    -- bar top relative to the row centre
local BAR_TRACK_ALPHA = 0x90
-- The word on a break's marker as it scrolls past ("instrumental · 0:15"),
-- and the countdown's label when there is no section to name.
local BREAK_WORD      = "instrumental"
local BREAK_NEW_WORD  = "in"             -- "CHORUS in 0:12": the next line starts a section
local BREAK_RESUME_WORD = "resumes in"   -- "VERSE 2 resumes in 0:12": the break falls mid-section

-- ── Outro ───────────────────────────────────────────────────────────────────
local OUTRO_HOLD_MS = 2500    -- stack stays after the last line ends (no outro bar)
local OUTRO_FADE_MS = 600     -- closing fade; also the outro bar's fade after the END marker

-- ── Instrumental outro ──────────────────────────────────────────────────────
-- Mark where the music ends with a line whose Effect field reads END: a
-- Comment line, placed in Aegisub on the waveform. When that is at least
-- BREAK_THRESHOLD after the last Main line ends, the song gets an outro item:
-- it rides up the stack as a dim marker like a break, lands in the active row,
-- then glides to OUTRO_Y while the rest of the stack fades out, and counts
-- down with a progress bar to the END time. No marker, or a shorter tail,
-- keeps the plain hold-and-fade above. The macro only ever sees the file, so
-- the end of the music has to be written into it; nothing reads the audio.
local OUTRO_MARKER        = "END"
local OUTRO_WORD          = "instrumental outro"
local OUTRO_JOINER        = "·"
local OUTRO_Y             = 540   -- where the outro settles: the frame's vertical centre
local OUTRO_GLIDE_MS      = 600   -- active row -> OUTRO_Y, eased, from the moment it lands
local OUTRO_STACK_FADE_MS = 600   -- the rest of the stack fades out over this, same start

-- ── BG lines ────────────────────────────────────────────────────────────────
-- A lone BG line sits at Y_BG. Two BG lines whose timing overlaps are stacked
-- in two slots, spreading downward from Y_BG.
local Y_BG                 = 880
local BG_LEADIN            = 750    -- ms the line shows, unsung, before its onset
local BG_MAX_SLOTS         = 2
local BG_SLOT_SPACING_MULT = 1.29   -- x BG fontsize = stacked row pitch (fs92: 118px)
local BG_STACK_SHIFT       = 2      -- px each row moves inward; effective pitch = pitch - 2*shift (fs92: 114px, = ROW_PITCH)
local BG_CUE_ENABLE        = true   -- entry-cue dots on BG lines, same gap rule as Main (CUE_GAP_MIN)

-- ── BG separator ────────────────────────────────────────────────────────────
-- A thin rule between the stack and the BG lines with a label at its centre.
-- Shown while a BG line is on screen (from SEP_LEAD_MS before its lead-in to
-- its end), in the BG secondary colour. BG windows closer together than
-- SEP_MERGE_MS are joined, so the rule doesn't blink off and on between quick
-- BG lines.
local SEP_ENABLE      = true
local SEP_Y           = 798     -- centre of the rule and label
local SEP_WIDTH       = 1104    -- full rule width, label gap included
local SEP_HEIGHT      = 6       -- px; half of BAR_HEIGHT
local SEP_LABEL       = "BACKGROUND VOCALS"
local SEP_FS          = 32      -- drawn without the style's outline
local SEP_FSP         = 7       -- letter-spacing on the label
local SEP_LABEL_PAD   = 23      -- px of clear space either side of the label
local SEP_LINE_ALPHA  = 0x80
local SEP_LABEL_ALPHA = 0x50
local SEP_FADE_MS     = 250
local SEP_MERGE_MS    = 1500
local SEP_LEAD_MS     = 750     -- rule appears this long before the BG line itself

-- ── Stack rows hidden while BG is on screen ──────────────────────────────────
-- The bottom BG_HIDE_ROWS rows of the stack fade out over BG_HIDE_FADE_MS,
-- ending as the separator starts to appear, and fade back in after it goes.
-- A row scrolling through that position is fully hidden once it is more than
-- BG_HIDE_RAMP px below the last row kept, so it never lingers part-faded
-- over the separator; it fades back in over those last px as it arrives.
local BG_HIDE_ROWS    = 1
local BG_HIDE_FADE_MS = 250
local BG_HIDE_RAMP    = 46

-- ── Fill ────────────────────────────────────────────────────────────────────
local MAIN_FILL_MODE = "sweep"      -- "sweep" | "instant"
local BG_FILL_MODE   = "sweep"

-- ── Font metrics ────────────────────────────────────────────────────────────
-- Consolas advance per character = (CHAR_ADV_RATIO * fontsize + spacing) * ScaleX/100.
-- Exact for a monospace face; used for the dots' x position and the
-- separator's label gap.
local CHAR_ADV_RATIO = 0.4698
-- Letter-spacing on every lyric line, Main and BG, written as an \fsp tag.
-- It has to be a tag: Aegisub will not take a negative Spacing in a style,
-- and libass ignores one there. At fontsize 92, -1.7 makes the average gap
-- between letters 80% of what it was at fontsize 80 with Spacing 1 (measured
-- on Consolas Bold renders). Section labels, countdowns and markers keep the
-- style's Spacing.
local LYRIC_FSP = -1.7

-- ── Layers (higher draws on top) ────────────────────────────────────────────
local LAYER_ROW     = 10
local LAYER_ACTIVE  = 20
local LAYER_SECTION = 8
local LAYER_BREAK   = 12
local LAYER_BAR     = 15
local LAYER_TIMER   = 17
local LAYER_DOTS    = 21
local LAYER_BG      = 5
local LAYER_SEP     = 4

-- Set in generate(): the merged BG windows { s, e } (separator timing), used
-- to hide the bottom stack rows while BG lines are on screen.
local HIDE_WINS = {}

-- ============================================================================
-- HELPERS
-- ============================================================================

-- A full copy of a line table, so a generated event never shares a field
-- with the line it was built from.
local function deepcopy(obj)
    if type(obj) ~= "table" then return obj end
    local res = {}
    for k, v in pairs(obj) do res[deepcopy(k)] = deepcopy(v) end
    return res
end

local function round(x) return math.floor(x + 0.5) end

-- Line text with every override block removed and the ends trimmed.
local function plain_text(text)
    text = text:gsub("{[^}]*}", "")
    text = text:gsub("^%s+", ""):gsub("%s+$", "")
    return text
end

-- An \alpha tag for a transparency 0..255, clamped and rounded.
local function alpha_tag(a)
    a = math.max(0, math.min(255, round(a)))
    return string.format("\\alpha&H%02X&", a)
end

-- Milliseconds as m:ss, rounded up to the next whole second.
local function fmt_mmss(ms)
    local s = math.max(0, math.ceil(ms / 1000))
    return string.format("%d:%02d", math.floor(s / 60), s % 60)
end

-- Width of one character of a lyric line in a style, px: the advance with
-- LYRIC_FSP in place of the style's Spacing (see CHAR_ADV_RATIO).
local function char_advance(style)
    return (CHAR_ADV_RATIO * style.fontsize + LYRIC_FSP) * (style.scale_x or 100) / 100
end

-- The \fsp tag every lyric line carries.
local FSP_TAG = string.format("\\fsp%g", LYRIC_FSP)

-- Quadratic ease-out: fast start, gentle stop.
local function ease(u)
    return 1 - (1 - u) * (1 - u)
end

-- ============================================================================
-- FILL MODE
-- ============================================================================

-- Rewrites every bare \k as \kf, so the line sweeps. \kf and \ko are left
-- alone and durations are never touched, so this is safe to apply to any
-- line. Needed because lines can come back from an Aegisub round trip
-- carrying bare \k, which renders as a jump rather than a sweep.
local function normalize_sweep(text)
    return (text:gsub("\\k(%d)", "\\kf%1"))
end

-- "sweep": normalize_sweep(). "instant": rewrite every karaoke tag as \k, so
-- each syllable fills at once; only done when every block in the line is a
-- plain karaoke tag, and anything else falls back to sweep with a log line.
local function apply_fill_mode(text, mode)
    if mode ~= "instant" then return normalize_sweep(text) end
    for blk in text:gmatch("{([^}]*)}") do
        if not blk:match("^\\k[fo]?%d+$") then
            aegisub.debug.out("FILL: non-karaoke tag block, keeping sweep: {" .. blk .. "}\n")
            return normalize_sweep(text)
        end
    end
    local out = {}
    for d, chunk in text:gmatch("{\\k[fo]?(%d+)}([^{]*)") do
        table.insert(out, string.format("{\\k%d}%s", tonumber(d), chunk))
    end
    if #out == 0 then return text end
    return table.concat(out)
end

-- ============================================================================
-- KARAOKE UNITS: parse, measure, re-emit at a time offset
-- ============================================================================

-- Parses a line's text into units: { tag = "kf"|"k"|"ko"|nil, dur = cs|nil,
-- pre = other override tags in the same block, text = string }.
local function parse_units(text)
    local units = {}
    local pos = 1
    local lead = text:match("^([^{]*)")
    if lead and lead ~= "" then
        table.insert(units, { text = lead, pre = "" })
        pos = #lead + 1
    end
    while pos <= #text do
        local s, e, blk = text:find("{([^}]*)}", pos)
        if not s then
            table.insert(units, { text = text:sub(pos), pre = "" })
            break
        end
        local after_e = text:find("{", e + 1, true)
        local chunk = text:sub(e + 1, (after_e or (#text + 1)) - 1)
        local tag, dur = blk:match("\\(k[fo]?)(%d+)")
        local pre = blk:gsub("\\k[fo]?%d+", "")
        table.insert(units, { tag = tag, dur = dur and tonumber(dur) or nil, pre = pre, text = chunk })
        pos = (after_e or (#text + 1))
    end
    return units
end

-- Sum of the karaoke durations, in ms.
local function units_total_ms(units)
    local cs = 0
    for _, u in ipairs(units) do if u.dur then cs = cs + u.dur end end
    return cs * 10
end

-- Lines are expected to carry a \k tag on every syllable (whisperx_to_ass.py
-- guarantees it), but a hand-added line can arrive untagged. Untagged text
-- would otherwise glue itself to the silent lead syllable units_at() prepends
-- and sweep during the wait. A wholly untagged line becomes one syllable
-- spanning the line; a leading untagged fragment on a tagged line becomes
-- \kf0.
local function normalize_units(units, line)
    local timed = 0
    for _, u in ipairs(units) do if u.dur then timed = timed + 1 end end
    if timed == 0 then
        local text = {}
        for _, u in ipairs(units) do table.insert(text, u.text) end
        local dur_cs = math.max(1, round((line.end_time - line.start_time) / 10))
        return { { tag = "kf", dur = dur_cs, pre = "", text = table.concat(text) } }
    end
    for _, u in ipairs(units) do
        if u.dur then break end
        u.tag, u.dur = "kf", 0
    end
    return units
end

-- The line's text with all tags removed.
local function units_plain(units)
    local out = {}
    for _, u in ipairs(units) do table.insert(out, u.text) end
    return plain_text(table.concat(out))
end

-- Re-emits the karaoke text as it should look in an event that starts
-- `elapsed_ms` after the line's own start time. Every syllable keeps its own
-- duration; only the karaoke clock is moved, by one empty syllable in front:
--   negative elapsed = event starts before the onset: a silent lead syllable
--     of that length, so the sweep waits for the onset;
--   positive elapsed = event starts mid-line: a lead syllable of NEGATIVE
--     length, which starts libass's karaoke clock that much earlier, so the
--     fill resumes exactly where the previous event left it.
--
-- Why not restart the syllable in progress with its remaining duration: a \kf
-- sweep always starts from the syllable's left edge, so the fill would snap
-- back and re-sweep at every event boundary, several times per scroll.
-- Cut into the scroll's pieces this way, a line renders pixel-identical to
-- the uncut line on every frame. Depends on the renderer honouring a negative
-- \kf; libass does. \kt is the fallback.
local function units_at(units, elapsed_ms)
    local out = {}
    local elapsed_cs = elapsed_ms / 10
    if elapsed_cs < 0 then
        table.insert(out, string.format("{\\kf%d}", math.ceil(-elapsed_cs)))
    elseif elapsed_cs > 0 then
        local skip = round(elapsed_cs)
        if skip > 0 then table.insert(out, string.format("{\\kf-%d}", skip)) end
    end
    for _, u in ipairs(units) do
        if u.dur then
            table.insert(out, string.format("{%s\\%s%d}%s", u.pre, u.tag or "kf", u.dur, u.text))
        else
            if u.pre ~= "" then table.insert(out, "{" .. u.pre .. "}") end
            table.insert(out, u.text)
        end
    end
    return table.concat(out)
end

-- ============================================================================
-- COLLECT LINES
-- ============================================================================

-- The END marker: OUTRO_MARKER as a word in the Effect field, on any style.
local function is_end_marker(line)
    local eff = " " .. (line.effect or ""):upper() .. " "
    return eff:find("[%s,]" .. OUTRO_MARKER .. "[%s,]") ~= nil
end

-- Collects the Main and BG dialogue lines, sorted by start time, and comments
-- out the originals so only the generated events render. Also finds the END
-- marker (the latest one, if there are several). Aborts on a collected line
-- that already carries \pos or \move: that is this macro's own output.
local function collect_lines(subs)
    local main_lines, bg_lines = {}, {}
    local main_idx, bg_idx = {}, {}
    local end_ms, n_end = nil, 0

    for i = 1, #subs do
        local line = subs[i]
        if line.class == "dialogue" and is_end_marker(line) then
            n_end = n_end + 1
            if not end_ms or line.start_time > end_ms then end_ms = line.start_time end
            -- A marker left as Dialogue would render whatever text it has, or be
            -- read as a lyric. It is a marker, so it is always a comment.
            if not line.comment then line.comment = true; subs[i] = line end
        elseif line.class == "dialogue" and not line.comment then
            if line.style == "Main" or line.style == "BG" then
                if line.text:find("\\pos(", 1, true) or line.text:find("\\move(", 1, true) then
                    aegisub.debug.out(
                        "ABORT: line " .. i .. " already carries \\pos/\\move — this file is macro OUTPUT.\n" ..
                        "Run the macro once, on the hand-timed _subs-timed.ass, never on a _subs-final.ass.\n")
                    aegisub.cancel()
                end
                if line.style == "Main" then
                    table.insert(main_lines, deepcopy(line)); table.insert(main_idx, i)
                else
                    table.insert(bg_lines, deepcopy(line)); table.insert(bg_idx, i)
                end
            end
        end
    end

    for _, i in ipairs(main_idx) do local l = subs[i]; l.comment = true; subs[i] = l end
    for _, i in ipairs(bg_idx)   do local l = subs[i]; l.comment = true; subs[i] = l end

    table.sort(main_lines, function(a, b) return a.start_time < b.start_time end)
    table.sort(bg_lines,   function(a, b) return a.start_time < b.start_time end)
    if n_end > 1 then
        aegisub.debug.out(string.format("WARNING: %d END markers — using the latest, at %dms\n", n_end, end_ms))
    end
    return main_lines, bg_lines, end_ms
end

-- ============================================================================
-- ITEM LIST + TIMELINE
-- ============================================================================

-- Builds the item list (breaks, section labels, lines and the outro), each
-- item's position in the stack, and the landing time A of every item that
-- can be active. A line's sweep comes from its karaoke durations, never its
-- end time; the end time measures gaps instead (breaks, cues, the outro), so
-- a stale end time can invent or suppress a break. Returns the items, the
-- active sequence, the end time of the last state, and the outro item (or
-- nil).
local function build_items(main_lines, styles, end_ms)
    local items = {}
    local prev_line = nil
    local current_section = nil

    for i, line in ipairs(main_lines) do
        local gap_from = prev_line and prev_line.end_time or 0
        local gap      = line.start_time - gap_from
        local starts_section = (line.actor and line.actor ~= "") and line.actor or nil

        if gap >= BREAK_THRESHOLD then
            -- Label: the section the next line starts, else the section the
            -- break interrupts (marked as resuming), else nothing.
            table.insert(items, {
                kind = "break", height = ROW_PITCH,
                from_ms = gap_from, to_ms = line.start_time,
                label  = starts_section or current_section,
                resume = (starts_section == nil) and (current_section ~= nil),
            })
        end
        if starts_section then
            table.insert(items, { kind = "section", height = SECTION_GAP, label = starts_section })
            current_section = starts_section
        end

        local units = normalize_units(parse_units(apply_fill_mode(line.text, MAIN_FILL_MODE)), line)
        table.insert(items, {
            kind = "line", height = ROW_PITCH, line = line, units = units,
            start = line.start_time, fin = line.start_time + units_total_ms(units),
            plain = units_plain(units), gap = gap,
            cue = gap >= CUE_GAP_MIN,
        })
        prev_line = line
    end

    -- Instrumental outro: from the last Main line's end to the END marker.
    local last_end = 0
    for _, line in ipairs(main_lines) do last_end = math.max(last_end, line.end_time) end
    local outro = nil
    if not end_ms then
        aegisub.debug.out("No END marker — no outro bar. (Comment line, Effect END, where the music ends.)\n")
    elseif end_ms - last_end >= BREAK_THRESHOLD then
        outro = { kind = "outro", height = ROW_PITCH, from_ms = last_end, to_ms = end_ms }
        table.insert(items, outro)
    elseif end_ms < last_end then
        aegisub.debug.out(string.format(
            "WARNING: END marker at %s is before the last line ends (%s) — ignored.\n",
            fmt_mmss(end_ms), fmt_mmss(last_end)))
    else
        aegisub.debug.out(string.format(
            "END marker: tail %.1fs is under BREAK_THRESHOLD — plain fade, no outro bar.\n",
            (end_ms - last_end) / 1000))
    end

    -- cumulative geometry: centre of each item relative to the top of the list
    local off = 0
    for _, it in ipairs(items) do
        it.centre = off + it.height / 2
        off = off + it.height
    end

    -- Provisional landing times for lines, breaks and the outro (before the
    -- monotonic clamp)
    for idx, it in ipairs(items) do
        if it.kind == "line" then
            it.lead = it.cue and CUE_MS or LEAD_MS
            it.A = it.start - it.lead
        elseif it.kind == "break" or it.kind == "outro" then
            it.A = it.from_ms + BREAK_HOLD_MS
        end
    end

    -- Sections pause in the active row only when the item before them is a
    -- line (not a break) and the next line lands late enough to leave room
    -- for a scroll in and a beat.
    if SECTION_PAUSE then
        for idx, it in ipairs(items) do
            if it.kind == "section" and idx > 1 and idx < #items then
                local before, after = items[idx - 1], items[idx + 1]
                if before.kind == "line" and after.kind == "line" then
                    local A = before.line.end_time + SECTION_HOLD_MS
                    if after.A - A >= SCROLL_MS + MIN_STATE_MS then
                        it.A = A
                        it.paused = true
                    end
                end
            end
        end
    end

    -- Active sequence, in item order, with the monotonic clamp: each landing
    -- at least SCROLL_MS + MIN_STATE_MS after the previous one. The first
    -- state starts at 0, so the stack is on screen from the start.
    local acts = {}
    for idx, it in ipairs(items) do
        if it.A then
            it.idx = idx
            table.insert(acts, it)
        end
    end
    for k, it in ipairs(acts) do
        if k == 1 then it.A = 0 end
        if k > 1 then
            it.A = math.max(it.A, acts[k - 1].A + SCROLL_MS + MIN_STATE_MS)
        end
        it.state = k
    end
    local last_line = main_lines[#main_lines]
    local A_end = math.max(acts[#acts].A + MIN_STATE_MS, last_line.end_time + OUTRO_HOLD_MS)
    if outro then
        -- the outro is always the last state, and it ends at the marker
        A_end = math.max(outro.A + OUTRO_GLIDE_MS + MIN_STATE_MS, outro.to_ms)
    end

    return items, acts, A_end, outro
end

-- y of item `it` when `act` is active
local function y_of(it, act)
    return Y_ACTIVE + (it.centre - act.centre)
end

-- Whether a row centre at y is within the visible rows (1px slack for rounding).
local function visible(y)
    return y >= Y_ACTIVE - LINES_ABOVE * ROW_PITCH - 1
       and y <= Y_ACTIVE + LINES_BELOW * ROW_PITCH + 1
end

-- Transparency of item `it` with its centre at y: by row distance from the
-- active row (ALPHA_ABOVE / ALPHA_BELOW), never more opaque than the item's
-- own base (section labels and break markers are always dimmer).
local function row_alpha(it, y)
    local d = round(math.abs(y - Y_ACTIVE) / ROW_PITCH)
    local base = 0
    if it.kind == "section" then base = SECTION_ALPHA end
    if it.kind == "break" or it.kind == "outro" then base = BREAK_MARKER_ALPHA end
    if d == 0 then
        if it.kind == "section" then return SECTION_ACTIVE_ALPHA end
        return base
    end
    local tbl = (y < Y_ACTIVE) and ALPHA_ABOVE or ALPHA_BELOW
    local a = tbl[math.min(d, #tbl)] or tbl[#tbl] or 0
    return math.max(a, base)
end

-- ============================================================================
-- EMISSION
-- ============================================================================

-- A copy of `template` with the given layer, times and text. Actor and
-- Effect are blanked, so section labels and SYLFIX / LOWCONF flags never
-- reach the render.
local function new_event(template, layer, t0, t1, text)
    local l = deepcopy(template)
    l.layer      = layer
    l.start_time = round(t0)
    l.end_time   = round(t1)
    l.text       = text
    l.actor      = ""
    l.effect     = ""
    return l
end

-- Text body and any extra tags for an item in a segment starting at t0 (ms).
-- Lines carry their karaoke tags until the fill is complete, whatever row they
-- sit in: an unsung line renders in the secondary colour through its \kf tags
-- (a silent lead syllable covers the time before the onset), a sung line is
-- plain text in the primary colour. No \c tag is ever needed on a line.
local function item_body(it, styles, t0)
    local st = styles["Main"]
    if it.kind == "line" then
        if t0 < it.fin then return units_at(it.units, t0 - it.start), FSP_TAG end
        return it.plain, FSP_TAG
    elseif it.kind == "section" then
        local label = SECTION_UPPER and it.label:upper() or it.label
        return label, string.format("\\fs%d%s\\c%s", SECTION_FS, SECTION_ITALIC and "\\i1" or "", st.color2)
    elseif it.kind == "break" then
        return string.format("%s · %s", BREAK_WORD, fmt_mmss(it.to_ms - it.from_ms)),
               string.format("\\fs%d\\c%s", BREAK_MARKER_FS, st.color2)
    elseif it.kind == "outro" then
        return string.format("%s %s %s", OUTRO_WORD, OUTRO_JOINER, fmt_mmss(it.to_ms - it.from_ms)),
               string.format("\\fs%d\\c%s", BREAK_MARKER_FS, st.color2)
    end
end

-- One event for one item over [t0, t1]: \pos, or \move when it travels from
-- y0 to y1; alpha a0, animated to a1 with \t when they differ. The layer
-- follows the item's kind, raised for a line sitting in the active row.
local function emit_piece(subs, template, styles, it, t0, t1, y0, y1, a0, a1)
    if t1 <= t0 then return end
    local body, extra = item_body(it, styles, t0)
    if not body then return end
    local pos
    if round(y0) == round(y1) then
        pos = string.format("\\pos(%d,%d)", X_CENTER, round(y0))
    else
        pos = string.format("\\move(%d,%d,%d,%d)", X_CENTER, round(y0), X_CENTER, round(y1))
    end
    local alpha = alpha_tag(a0)
    if round(a0) ~= round(a1) then
        alpha = alpha .. string.format("\\t(%s)", alpha_tag(a1))
    end
    local layer = LAYER_ROW
    if it.kind == "section" then layer = LAYER_SECTION end
    if it.kind == "break" or it.kind == "outro" then layer = LAYER_BREAK end
    if it.kind == "line" and round(y0) == Y_ACTIVE and round(y1) == Y_ACTIVE then layer = LAYER_ACTIVE end
    local text = string.format("{\\an5%s%s%s}%s", pos, extra, alpha, body)
    subs.append(new_event(template, layer, t0, t1, text))
end

-- 0..1: how far into the hidden zone a row at y sits. 0 at the last kept row,
-- 1 once it is BG_HIDE_RAMP px below that, so a row scrolling through is
-- fully hidden for nearly all of its travel.
local function hide_zone(y)
    local base = Y_ACTIVE + (LINES_BELOW - BG_HIDE_ROWS) * ROW_PITCH
    local z = (y - base) / BG_HIDE_RAMP
    if z < 0 then return 0 elseif z > 1 then return 1 end
    return z
end

-- 0..1: how hidden the zone is at time t. Ramps up over the BG_HIDE_FADE_MS
-- before a BG window starts and back down over the same after it ends.
local function hide_amount(t)
    local f = 0
    for _, w in ipairs(HIDE_WINS) do
        local a, b = w.s - BG_HIDE_FADE_MS, w.e + BG_HIDE_FADE_MS
        if t > a and t < b then
            local v = 1
            if t < w.s then v = (t - a) / BG_HIDE_FADE_MS
            elseif t > w.e then v = (b - t) / BG_HIDE_FADE_MS end
            if v > f then f = v end
        end
    end
    return f
end

-- A stack segment, cut at the hide ramps' corners where it reaches the
-- hidden zone, with its alpha pushed toward invisible by the amount hidden.
-- Pieces invisible from end to end are not emitted.
local function emit_segment(subs, template, styles, it, t0, t1, y0, y1, a0, a1)
    if t1 <= t0 then return end
    if BG_HIDE_ROWS <= 0 or #HIDE_WINS == 0 or hide_zone(math.max(y0, y1)) <= 0 then
        return emit_piece(subs, template, styles, it, t0, t1, y0, y1, a0, a1)
    end
    local cuts = { t0, t1 }
    for _, w in ipairs(HIDE_WINS) do
        local cs = { w.s - BG_HIDE_FADE_MS, w.s, w.e, w.e + BG_HIDE_FADE_MS }
        for _, c in ipairs(cs) do
            if c > t0 and c < t1 then table.insert(cuts, c) end
        end
    end
    table.sort(cuts)
    for i = 1, #cuts - 1 do
        local ta, tb = cuts[i], cuts[i + 1]
        if tb - ta >= 1 then
            local ua, ub = (ta - t0) / (t1 - t0), (tb - t0) / (t1 - t0)
            local ya, yb = y0 + (y1 - y0) * ua, y0 + (y1 - y0) * ub
            local aa, ab = a0 + (a1 - a0) * ua, a0 + (a1 - a0) * ub
            aa = aa + (255 - aa) * hide_amount(ta) * hide_zone(ya)
            ab = ab + (255 - ab) * hide_amount(tb) * hide_zone(yb)
            if round(aa) < 255 or round(ab) < 255 then
                emit_piece(subs, template, styles, it, ta, tb, ya, yb, aa, ab)
            end
        end
    end
end

-- Entry-cue dots left of a centred line: run from t_from (at most CUE_MS
-- before the onset) and go out one by one, the last at the onset. The line's
-- width is taken from its length in bytes, so a non-ASCII character (a curly
-- apostrophe, say) puts the dots slightly further left than its true width.
local function emit_cue_dots(subs, template, color, plain, adv, onset, y_centre, t_from)
    local left = X_CENTER - (#plain * adv) / 2
    local t_on = math.max(t_from, onset - CUE_MS)
    if t_on >= onset then return end
    local draw = string.format("m 0 0 l %d 0 %d %d 0 %d", DOT_SIZE, DOT_SIZE, DOT_SIZE, DOT_SIZE)
    for j = 1, DOT_COUNT do
        local x = left - DOT_MARGIN - (DOT_COUNT - j) * (DOT_SIZE + DOT_GAP) - DOT_SIZE
        local y = y_centre - DOT_SIZE / 2
        local off = onset - (DOT_COUNT - j) * (CUE_MS / DOT_COUNT)
        if off > t_on then
            local text = string.format(
                "{\\an7\\pos(%d,%d)\\p1\\fscx100\\fscy100\\bord0\\shad0\\c%s}%s",
                round(x), round(y), color, draw)
            subs.append(new_event(template, LAYER_DOTS, t_on, off, text))
        end
    end
end

-- Entry-cue dots beside a line sitting in the active row.
local function emit_dots(subs, template, styles, it, t_from)
    local st = styles["Main"]
    emit_cue_dots(subs, template, st.color1, it.plain, char_advance(st), it.start, Y_ACTIVE, t_from)
end

-- Countdown + progress bar for an active break or outro over [t0, t1], with
-- its row centre moving linearly from y0 to y1 (both default to the active
-- row, which is the static break case). A break counts down to the next
-- line's onset, an outro to the END marker.
local function emit_break_active(subs, template, styles, it, t0, t1, y0, y1)
    if t1 <= t0 then return end
    y0 = y0 or Y_ACTIVE
    y1 = y1 or y0
    local st = styles["Main"]
    local label, joiner
    if it.kind == "outro" then
        label, joiner = OUTRO_WORD, OUTRO_JOINER
    else
        label = it.label and (SECTION_UPPER and it.label:upper() or it.label) or BREAK_WORD
        joiner = it.resume and BREAK_RESUME_WORD or BREAK_NEW_WORD
    end
    -- \pos, or \move when the row centre travels during [a, b]
    local function place(x, dy, a, b)
        local ya = round(y0 + (y1 - y0) * (a - t0) / (t1 - t0) + dy)
        local yb = round(y0 + (y1 - y0) * (b - t0) / (t1 - t0) + dy)
        if ya == yb then return string.format("\\pos(%d,%d)", round(x), ya) end
        return string.format("\\move(%d,%d,%d,%d)", round(x), ya, round(x), yb)
    end

    -- countdown, one event per second, boundaries on whole seconds before the end
    local r = math.ceil((it.to_ms - t0) / 1000)
    while r >= 1 do
        local s0 = math.max(t0, it.to_ms - r * 1000)
        local s1 = math.min(t1, it.to_ms - (r - 1) * 1000)
        if s1 > s0 then
            local text = string.format("{\\an5%s\\fs%d\\c%s}%s %s %s",
                place(X_CENTER, BREAK_TIMER_DY, s0, s1), BREAK_TIMER_FS, st.color1,
                label, joiner, fmt_mmss(r * 1000))
            subs.append(new_event(template, LAYER_TIMER, s0, s1, text))
        end
        r = r - 1
        if s1 >= t1 then break end
    end

    -- bar: track + fill animated with \t(\fscx)
    local bx = X_CENTER - BAR_WIDTH / 2
    local draw = string.format("m 0 0 l %d 0 %d %d 0 %d", BAR_WIDTH, BAR_WIDTH, BAR_HEIGHT, BAR_HEIGHT)
    local at = place(bx, BAR_DY, t0, t1)
    subs.append(new_event(template, LAYER_BAR, t0, t1, string.format(
        "{\\an7%s\\p1\\fscx100\\fscy100\\bord0\\shad0\\c%s%s}%s",
        at, st.color2, alpha_tag(BAR_TRACK_ALPHA), draw)))
    local total = it.to_ms - it.from_ms
    local p0 = 100 * (t0 - it.from_ms) / total
    local p1 = 100 * (t1 - it.from_ms) / total
    subs.append(new_event(template, LAYER_BAR + 1, t0, t1, string.format(
        "{\\an7%s\\p1\\fscx%.2f\\fscy100\\bord0\\shad0\\c%s\\t(\\fscx%.2f)}%s",
        at, p0, st.color1, p1, draw)))
end

-- The outro state: every other visible item fades out while the outro glides
-- from the active row to OUTRO_Y, then it counts down to the END marker; the
-- full bar then fades over OUTRO_FADE_MS. Replaces the generic static window
-- and closing fade for this one state.
local function emit_outro_state(subs, template, styles, items, act, A_end)
    local t0 = act.A
    for _, it in ipairs(items) do
        if it ~= act then
            local y = y_of(it, act)
            if visible(y) then
                emit_segment(subs, template, styles, it, t0, t0 + OUTRO_STACK_FADE_MS,
                    y, y, row_alpha(it, y), 255)
            end
        end
    end
    local segs = math.max(1, EASE_SEGMENTS)
    for s = 1, segs do
        local u0, u1 = (s - 1) / segs, s / segs
        emit_break_active(subs, template, styles, act,
            t0 + OUTRO_GLIDE_MS * u0, t0 + OUTRO_GLIDE_MS * u1,
            Y_ACTIVE + (OUTRO_Y - Y_ACTIVE) * ease(u0),
            Y_ACTIVE + (OUTRO_Y - Y_ACTIVE) * ease(u1))
    end
    emit_break_active(subs, template, styles, act, t0 + OUTRO_GLIDE_MS, act.to_ms, OUTRO_Y, OUTRO_Y)

    -- the finished bar fades out after the marker
    local st = styles["Main"]
    local bx, by = X_CENTER - BAR_WIDTH / 2, OUTRO_Y + BAR_DY
    local draw = string.format("m 0 0 l %d 0 %d %d 0 %d", BAR_WIDTH, BAR_WIDTH, BAR_HEIGHT, BAR_HEIGHT)
    local fade = string.format("\\fad(0,%d)", OUTRO_FADE_MS)
    local t1 = math.max(act.to_ms, A_end)
    subs.append(new_event(template, LAYER_BAR + 1, t1, t1 + OUTRO_FADE_MS, string.format(
        "{\\an7\\pos(%d,%d)\\p1\\fscx100\\fscy100\\bord0\\shad0\\c%s%s}%s",
        round(bx), round(by), st.color1, fade, draw)))
end

-- ============================================================================
-- STACK BUILDER
-- ============================================================================

-- Emits the whole stack. For each state, every visible item gets its static
-- window and its eased transition into the next state (after the last state,
-- a linear closing fade instead); the outro state has its own emitter; an
-- active line with a cue gets its dots. Then logs a summary.
local function build_stack(subs, main_lines, styles, end_ms)
    local items, acts, A_end, outro = build_items(main_lines, styles, end_ms)
    local template = main_lines[1]
    local n = #acts
    local n_cues, n_breaks, n_sections, n_paused = 0, 0, 0, 0
    for _, it in ipairs(items) do
        if it.kind == "break" then n_breaks = n_breaks + 1 end
        if it.kind == "section" then n_sections = n_sections + 1 end
        if it.kind == "section" and it.paused then n_paused = n_paused + 1 end
        if it.kind == "line" and it.cue then n_cues = n_cues + 1 end
    end

    for k = 1, n do
        local act    = acts[k]
        local nxt    = acts[k + 1]
        local t_stat0 = act.A
        local t_stat1 = nxt and (nxt.A - SCROLL_MS) or A_end

        if act.kind == "outro" then
            emit_outro_state(subs, template, styles, items, act, A_end)
        else
            for _, it in ipairs(items) do
                local y0 = y_of(it, act)
                local v0 = visible(y0)
                local y1, v1
                if nxt then
                    y1 = y_of(it, nxt); v1 = visible(y1)
                else
                    y1 = y0; v1 = false   -- closing fade (no outro bar)
                end
                if v0 or v1 then
                    local a0 = v0 and row_alpha(it, y0) or 255
                    local a1 = v1 and row_alpha(it, y1) or 255

                    -- static window
                    if v0 then
                        if it.kind == "break" and it == act then
                            emit_break_active(subs, template, styles, it, t_stat0, t_stat1)
                        else
                            emit_segment(subs, template, styles, it, t_stat0, t_stat1, y0, y0, a0, a0)
                        end
                    end

                    -- transition (or closing fade)
                    local m0 = nxt and (nxt.A - SCROLL_MS) or A_end
                    local m1 = nxt and nxt.A or (A_end + OUTRO_FADE_MS)
                    local segs = math.max(1, EASE_SEGMENTS)
                    if not nxt then segs = 1 end
                    for s = 1, segs do
                        local u0, u1 = (s - 1) / segs, s / segs
                        local f0, f1 = ease(u0), ease(u1)
                        if not nxt then f0, f1 = u0, u1 end
                        emit_segment(subs, template, styles, it,
                            m0 + (m1 - m0) * u0, m0 + (m1 - m0) * u1,
                            y0 + (y1 - y0) * f0, y0 + (y1 - y0) * f1,
                            a0 + (a1 - a0) * f0, a0 + (a1 - a0) * f1)
                    end
                end
            end
        end   -- outro state / every other state

        if act.kind == "line" and act.cue then
            emit_dots(subs, template, styles, act, t_stat0)
        end
    end

    aegisub.debug.out(string.format(
        "v2 stack: %d lines, %d sections (%d paused in the active row), %d breaks, %d cued entries, %d states, ends %dms\n",
        #main_lines, n_sections, n_paused, n_breaks, n_cues, n, A_end))
    for _, it in ipairs(items) do
        if it.kind == "break" then
            aegisub.debug.out(string.format("  break %s..%s (%s%s) active from %dms\n",
                fmt_mmss(it.from_ms), fmt_mmss(it.to_ms), it.label or BREAK_WORD,
                it.resume and ", resumes" or "", it.A))
        end
    end
    if outro then
        aegisub.debug.out(string.format("  outro %s..%s (%s) active from %dms\n",
            fmt_mmss(outro.from_ms), fmt_mmss(outro.to_ms), fmt_mmss(outro.to_ms - outro.from_ms), outro.A))
    end
end

-- ============================================================================
-- BG LINES
-- ============================================================================

-- Which BG lines overlap another BG line in their own timing. Only literal
-- overlap counts; lead-ins never make a line stacked.
local function compute_bg_stacked(bg_lines)
    local stacked = {}
    for i = 1, #bg_lines do stacked[i] = false end
    for i = 1, #bg_lines do
        for j = i + 1, #bg_lines do
            if bg_lines[j].start_time < bg_lines[i].end_time
               and bg_lines[i].start_time < bg_lines[j].end_time then
                stacked[i] = true
                stacked[j] = true
            end
        end
    end
    return stacked
end

-- A slot (row) and a lead-in start for each BG line, plus whether it gets
-- cue dots. A line takes the first free slot; with none free it shares the
-- one that frees earliest, with a warning. A lead-in never starts before the
-- slot's previous occupant has ended.
local function assign_bg_slots(bg_lines)
    local slot_end = {}
    for s = 1, BG_MAX_SLOTS do slot_end[s] = -math.huge end
    local slots, lead_starts, cues = {}, {}, {}
    local prev_end = 0
    for i, line in ipairs(bg_lines) do
        -- same rule as Main: a long enough gap since the previous BG line ends
        -- gets the dots, and its lead-in stretches to CUE_MS to carry them
        local cue = BG_CUE_ENABLE and (line.start_time - prev_end >= CUE_GAP_MIN)
        local lead = cue and CUE_MS or BG_LEADIN
        prev_end = math.max(prev_end, line.end_time)
        local chosen = nil
        for s = 1, BG_MAX_SLOTS do
            if slot_end[s] <= line.start_time then chosen = s; break end
        end
        if not chosen then
            local earliest = 1
            for s = 2, BG_MAX_SLOTS do
                if slot_end[s] < slot_end[earliest] then earliest = s end
            end
            chosen = earliest
            aegisub.debug.out(string.format(
                "WARNING: BG line %d (%d-%d) exceeds %d concurrent slots -- will pile up.\n",
                i, line.start_time, line.end_time, BG_MAX_SLOTS))
        end
        local lead_start = math.max(0, line.start_time - lead)
        lead_start = math.max(lead_start, slot_end[chosen])
        lead_starts[i]   = lead_start
        cues[i]          = cue
        slots[i]         = chosen
        slot_end[chosen] = math.max(slot_end[chosen], line.end_time)
    end
    return slots, lead_starts, cues
end

-- y position, lead-in start, slot and cue flag for each BG line. A line
-- that is not stacked sits at Y_BG whatever its slot; a stacked one sits one
-- slot pitch lower per slot, pulled inward by BG_STACK_SHIFT.
local function compute_bg_layout(bg_lines, styles)
    local stacked            = compute_bg_stacked(bg_lines)
    local slots, lead_starts, cues = assign_bg_slots(bg_lines)
    local pitch              = math.floor(styles["BG"].fontsize * BG_SLOT_SPACING_MULT)
    local layout = {}
    for i = 1, #bg_lines do
        local y = Y_BG
        if stacked[i] then
            y = Y_BG + ((slots[i] - 1) * pitch)
            if slots[i] == 1 then
                y = y + BG_STACK_SHIFT
            elseif slots[i] == BG_MAX_SLOTS then
                y = y - BG_STACK_SHIFT
            end
        end
        layout[i] = { y = y, lead_start = lead_starts[i], slot = slots[i], stacked = stacked[i], cue = cues[i] }
    end
    return layout
end

-- BG windows: each BG line runs from SEP_LEAD_MS before its lead-in to its
-- end; overlapping windows, and windows less than SEP_MERGE_MS apart, are
-- joined into one. Drives both the separator and the hidden stack rows.
local function bg_windows(bg_lines, layout)
    local wins = {}
    for i, line in ipairs(bg_lines) do
        table.insert(wins, { s = math.max(0, layout[i].lead_start - SEP_LEAD_MS), e = line.end_time })
    end
    table.sort(wins, function(a, b) return a.s < b.s end)
    local merged = {}
    for _, w in ipairs(wins) do
        local last = merged[#merged]
        if last and w.s - last.e < SEP_MERGE_MS then
            last.e = math.max(last.e, w.e)
        else
            table.insert(merged, { s = w.s, e = w.e })
        end
    end
    return merged
end

-- Separator rule + label over each BG window. The rule is one drawing with
-- two segments, leaving a gap at the centre for the label.
local function emit_bg_separator(subs, bg_lines, merged, styles)
    if not SEP_ENABLE or #bg_lines == 0 then return end
    local st = styles["BG"]

    local adv     = (CHAR_ADV_RATIO * SEP_FS + SEP_FSP) * (st.scale_x or 100) / 100
    local label_w = #SEP_LABEL * adv
    local half    = SEP_WIDTH / 2
    local gap     = label_w / 2 + SEP_LABEL_PAD
    local seg     = round(half - gap)            -- length of each segment
    local x0      = round(X_CENTER - half)
    local r0      = round(half + gap)            -- right segment start, relative to x0
    local r1      = round(SEP_WIDTH)
    local y0      = round(SEP_Y - SEP_HEIGHT / 2)
    local draw = string.format("m 0 0 l %d 0 %d %d 0 %d m %d 0 l %d 0 %d %d %d %d",
        seg, seg, SEP_HEIGHT, SEP_HEIGHT,
        r0, r1, r1, SEP_HEIGHT, r0, SEP_HEIGHT)

    local template = bg_lines[1]
    for _, w in ipairs(merged) do
        local fade = string.format("\\fad(%d,%d)", SEP_FADE_MS, SEP_FADE_MS)
        subs.append(new_event(template, LAYER_SEP, w.s, w.e, string.format(
            "{\\an7\\pos(%d,%d)\\p1\\fscx100\\fscy100\\bord0\\shad0\\c%s%s%s}%s",
            x0, y0, st.color2, alpha_tag(SEP_LINE_ALPHA), fade, draw)))
        subs.append(new_event(template, LAYER_SEP, w.s, w.e, string.format(
            "{\\an5\\pos(%d,%d)\\fs%d\\fsp%d\\bord0\\c%s%s%s}%s",
            X_CENTER, SEP_Y, SEP_FS, SEP_FSP, st.color2, alpha_tag(SEP_LABEL_ALPHA), fade, SEP_LABEL)))
    end
    aegisub.debug.out(string.format("BG separator: %d window(s)\n", #merged))
end

-- Emits each BG line: a lead-in in the secondary colour from its lead-in
-- start to its onset, then the line itself with its karaoke tags (keeping
-- its own start and end), and cue dots where it has a cue; then the separator.
local function build_bg_leadin(subs, bg_lines, styles, layout, wins)
    if #bg_lines == 0 then return end
    for i, line in ipairs(bg_lines) do
        local y = layout[i].y
        local secondary = styles["BG"].color2
        local lead_start = layout[i].lead_start
        if lead_start < line.start_time then
            local lead = deepcopy(line)
            lead.layer      = LAYER_BG
            lead.start_time = lead_start
            lead.end_time   = line.start_time
            lead.text = string.format("{\\an5\\pos(%d,%d)%s\\c%s}%s",
                X_CENTER, y, FSP_TAG, secondary, plain_text(line.text))
            subs.append(lead)
        end
        local active = deepcopy(line)
        active.layer = LAYER_BG
        active.text  = string.format("{\\an5\\pos(%d,%d)%s}%s",
            X_CENTER, y, FSP_TAG, apply_fill_mode(line.text, BG_FILL_MODE))
        subs.append(active)
        if layout[i].cue then
            local st = styles["BG"]
            emit_cue_dots(subs, line, st.color1, plain_text(line.text), char_advance(st),
                line.start_time, y, lead_start)
        end
    end
    emit_bg_separator(subs, bg_lines, wins, styles)
end

-- ============================================================================
-- ENTRY POINT
-- ============================================================================

local function generate(subs)
    local meta, styles = karaskel.collect_head(subs)
    if not styles["Main"] then
        aegisub.debug.out("ABORT: no style named Main in this file.\n")
        aegisub.cancel()
    end
    local main_lines, bg_lines, end_ms = collect_lines(subs)
    if #main_lines == 0 then
        aegisub.debug.out("ABORT: no Dialogue lines styled Main (comments are skipped).\n")
        aegisub.cancel()
    end
    local layout, wins = nil, {}
    if #bg_lines > 0 then
        layout = compute_bg_layout(bg_lines, styles)
        wins   = bg_windows(bg_lines, layout)
    end
    HIDE_WINS = wins
    build_stack(subs, main_lines, styles, end_ms)
    build_bg_leadin(subs, bg_lines, styles, layout, wins)
    aegisub.debug.out(string.format("Generated from %d Main and %d BG lines\n", #main_lines, #bg_lines))
end

aegisub.register_macro("Karaoke Formatting v2", "Scrolling stack, sections, breaks, BG", generate)
