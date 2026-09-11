"""Plan small, chronological excerpts without loading or copying audio."""


def evidence_intervals(rows, duration=0, max_gap=5.0, pad=1.0):
    intervals = []
    for row in sorted(rows, key=lambda r: (r["start"], r["end"])):
        start, end = max(0.0, row["start"]), row["end"]
        if end <= start:
            continue
        if intervals and start - intervals[-1]["end"] <= max_gap:
            intervals[-1]["end"] = max(end, intervals[-1]["end"])
        else:
            intervals.append(dict(start=start, end=end))
    for interval in intervals:
        interval["start"] = max(0.0, interval["start"] - pad)
        end = interval["end"] + pad
        interval["end"] = min(end, duration) if duration > 0 else end
    return [i for i in intervals if i["end"] > i["start"]]
