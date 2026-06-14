from dataclasses import dataclass, field
from validator import LayerResult

LAYER_ORDER = ["base", "interpreter", "functional"]

STATUS_COLOR = {
    "PASS":    "\033[92m",   # green
    "FAIL":    "\033[91m",   # red
    "SKIPPED": "\033[93m",   # yellow
    "NO_DATA": "\033[90m",   # grey
}
RESET = "\033[0m"


@dataclass
class TestResult:
    name: str
    pipeline: str
    layers: dict[str, LayerResult] = field(default_factory=dict)

    @property
    def overall(self) -> str:
        statuses = [self.layers[l].status for l in LAYER_ORDER if l in self.layers]
        if any(s == "FAIL"    for s in statuses): return "FAIL"
        if any(s == "PASS"    for s in statuses): return "PASS"
        if any(s == "NO_DATA" for s in statuses): return "NO_DATA"
        return "SKIPPED"


def print_report(results: list[TestResult]) -> bool:
    """Prints the full report. Returns True if all tests passed."""
    width = 60
    print("\n" + "=" * width)
    print("  OMS SMOKE TEST REPORT")
    print("=" * width)

    all_pass = True

    for r in results:
        color = STATUS_COLOR.get(r.overall, "")
        print(f"\n  ▶ {r.name}  [{r.pipeline}]  →  {color}{r.overall}{RESET}")
        print("  " + "-" * (width - 2))

        for layer in LAYER_ORDER:
            lr = r.layers.get(layer)
            if lr is None:
                continue
            c = STATUS_COLOR.get(lr.status, "")
            print(f"    {layer.upper():<14} {c}{lr.status}{RESET}")

            for chk in lr.checks:
                cs = STATUS_COLOR.get(chk["status"], "")
                value = chk.get("value")
                value_detail = f" value={value}" if value is not None else ""
                detail = chk.get("detail", "")
                if detail:
                    detail = f"  ← {detail}{value_detail}"
                elif value_detail:
                    detail = f"  ←{value_detail}"
                print(f"      · {chk['signal']:<28} {cs}{chk['status']}{RESET}{detail}")

        if r.overall == "FAIL":
            all_pass = False

    print("\n" + "=" * width)
    overall_color = STATUS_COLOR["PASS"] if all_pass else STATUS_COLOR["FAIL"]
    label = "ALL PASSED" if all_pass else "SOME TESTS FAILED"
    print(f"  {overall_color}{label}{RESET}")
    print("=" * width + "\n")

    return all_pass


def generate_html_report(results: list[TestResult], output_file: str) -> None:
    """Generate an HTML formatted report and write to file."""
    html_lines = [
        "<!DOCTYPE html>",
        "<html>",
        "<head>",
        "  <meta charset='UTF-8'>",
        "  <title>OMS Smoke Test Report</title>",
        "  <style>",
        "    body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }",
        "    .header { background-color: #333; color: white; padding: 15px; border-radius: 5px; }",
        "    .testcase { background-color: white; margin: 15px 0; padding: 15px; border-radius: 5px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }",
        "    .testcase-title { font-size: 18px; font-weight: bold; margin-bottom: 10px; }",
        "    .PASS { color: #28a745; font-weight: bold; }",
        "    .FAIL { color: #dc3545; font-weight: bold; }",
        "    .SKIPPED { color: #ffc107; font-weight: bold; }",
        "    .NO_DATA { color: #6c757d; font-weight: bold; }",
        "    .layer { margin-left: 20px; margin-top: 10px; }",
        "    .layer-name { font-weight: bold; font-size: 14px; margin-bottom: 5px; }",
        "    .check { margin-left: 40px; margin-top: 5px; font-size: 13px; }",
        "    .check-signal { font-family: monospace; }",
        "    .check-detail { margin-left: 10px; font-size: 12px; color: #666; }",
        "    .overall { margin-top: 20px; font-size: 16px; font-weight: bold; padding: 10px; border-radius: 5px; }",
        "    .overall-pass { background-color: #d4edda; color: #155724; }",
        "    .overall-fail { background-color: #f8d7da; color: #721c24; }",
        "  </style>",
        "</head>",
        "<body>",
        "  <div class='header'>",
        "    <h1>OMS SMOKE TEST REPORT</h1>",
        "  </div>",
    ]

    all_pass = True

    for r in results:
        status_class = r.overall
        html_lines.append(f"  <div class='testcase'>")
        html_lines.append(f"    <div class='testcase-title'>{r.name} [{r.pipeline}] <span class='{status_class}'>{r.overall}</span></div>")

        for layer in LAYER_ORDER:
            lr = r.layers.get(layer)
            if lr is None:
                continue
            html_lines.append(f"    <div class='layer'>")
            html_lines.append(f"      <div class='layer-name'>{layer.upper()}: <span class='{lr.status}'>{lr.status}</span></div>")

            for chk in lr.checks:
                value = chk.get("value")
                detail = chk.get("detail", "")
                value_detail = f" value={value}" if value is not None else ""
                full_detail = f"{detail}{value_detail}" if detail or value_detail else ""

                html_lines.append(f"      <div class='check'>")
                html_lines.append(f"        <span class='check-signal'>{chk['signal']}</span> <span class='{chk['status']}'>{chk['status']}</span>")
                if full_detail:
                    html_lines.append(f"        <div class='check-detail'>← {full_detail}</div>")
                html_lines.append(f"      </div>")

            html_lines.append(f"    </div>")

        html_lines.append(f"  </div>")

        if r.overall == "FAIL":
            all_pass = False

    overall_class = "overall-pass" if all_pass else "overall-fail"
    label = "ALL PASSED" if all_pass else "SOME TESTS FAILED"
    html_lines.append(f"  <div class='overall {overall_class}'>")
    html_lines.append(f"    {label}")
    html_lines.append(f"  </div>")

    html_lines.append("</body>")
    html_lines.append("</html>")

    with open(output_file, "w") as f:
        f.write("\n".join(html_lines))
    print(f"HTML report written to: {output_file}")
