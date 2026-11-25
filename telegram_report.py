"""Telegram Bot API 10.1+ native rich-table report rendering.

The screenshot-like bordered tables use sendRichMessage / editMessageText with
rich_message.html.  No image/font dependency is needed.  The caller keeps the
legacy sendMessage path as fallback if a Telegram server rejects rich content.
Each page has two columns so that Chinese node names and IPs wrap on phones.
"""
import html


MAX_ROWS_PER_PAGE = 14


def _escape(value):
    if value is None or value == "":
        return "—"
    return html.escape(str(value), quote=True)


def _num(value, digits=1, suffix=""):
    try:
        return ("%%.%df" % digits) % float(value) + suffix
    except (ValueError, TypeError, OverflowError):
        return "—"


def _table(headers, rows):
    if not rows:
        rows = [("暂无数据", "—")]
    head = "<tr>%s</tr>" % "".join("<th>%s</th>" % _escape(h) for h in headers)
    body = "".join(
        "<tr>%s</tr>" % "".join(
            '<td%s>%s</td>' % (
                ' align="right"' if col == 1 and headers[0] in ("项目", "指标") else "",
                cell,
            )
            for col, cell in enumerate(row)
        )
        for row in rows
    )
    return "<table bordered compact>%s%s</table>" % (head, body)


def _row(name, value):
    return (_escape(name), _escape(value))


def _title(title):
    return "<h3>%s</h3>" % _escape(title)


def _paragraph(text):
    return "<p>%s</p>" % _escape(text)


def _single_report(report, error):
    ip = report.get("ip") or "未知"
    result = report.get("result") or {}
    if error or not result:
        reason = report.get("measurement_error") or error or "未收到有效下载数据"
        rows = [_row("IP", ip), _row("结果", "失败 / 无效"), _row("原因", reason)]
        if report.get("sampled_seconds") is not None:
            rows.append(_row("有效采样", _num(report["sampled_seconds"], 1, " / 30 秒")))
        return [_title("❌ CF 单 IP 测速") + _table(("项目", "结果"), rows)]
    rows = [
        _row("测试 IP", ip),
        _row("Cloudflare 机房", result.get("colo") or "未知"),
        _row("均速 (5–30 秒)", _num(result.get("average_mbps"), 2, " Mbps")),
        _row("中位速度", _num(result.get("median_mbps"), 2, " Mbps")),
        _row("TCP 延迟", _num(result.get("tcp_ms"), 1, " ms")),
        _row("波动系数", _num(
            100 * float(result.get("variation_coefficient") or 0), 1, "%"
        )),
        _row("稳定性", "稳定" if result.get("stable") else "波动较大"),
    ]
    applied = report.get("applied_nodes") or {}
    rows.append(_row(
        "配置写入",
        ("已更新 %d 个节点" % len(applied)) if applied
        else ("失败" if report.get("apply_error") else "保留原配置"),
    ))
    page = _title("✅ CF 单 IP 测速结果")
    page += _table(("项目", "结果"), rows)
    if applied:
        page += "<h4>节点更新</h4>"
        page += _table(("节点名称", "新 CF IP"), [
            (_escape(name), "<code>%s</code>" % _escape(address))
            for name, address in applied.items()
        ])
    elif report.get("apply_reason"):
        page += _paragraph(report["apply_reason"])
    if report.get("apply_error"):
        page += _paragraph("配置写入失败：" + str(report["apply_error"]))
    return [page]


def _status_for_attempt(item, tier):
    state = item.get("status")
    if state == "early_discard":
        return "提前淘汰"
    if state == "incomplete":
        return "采样不完整"
    if state in ("speed_test_failed", "failed"):
        return "下载失败"
    if state != "measured" and item.get("stable_mbps") is None:
        return "未测出速度"
    if not item.get("stable", True):
        return "不稳定"
    speed = item.get("average_mbps", item.get("stable_mbps"))
    try:
        return "达标" if tier is not None and float(speed) >= float(tier) else "已测量"
    except (TypeError, ValueError):
        return "已测量"


def _attempt_rows(report, tier):
    attempts = []
    for run in report.get("speed_test_rounds") or []:
        round_number = run.get("round", "?")
        for item in run.get("results") or []:
            attempts.append((round_number, item))
    if not attempts:
        for item in report.get("candidates") or []:
            for result in item.get("speed_test_attempts") or []:
                attempts.append(("?", result))
    rows = []
    for round_number, item in attempts:
        ip = item.get("ip", "未知")
        colo = item.get("colo") or "—"
        left = "<code>%s</code><br>%s · %s" % (
            _escape(ip), _escape(colo), _escape("第%s轮" % round_number),
        )
        status = _status_for_attempt(item, tier)
        if item.get("stable_mbps") is not None:
            speed = _num(
                item.get("average_mbps", item.get("stable_mbps")), 1, " Mbps"
            )
            right = "<b>%s</b><br>%s · RTT %s" % (
                _escape(speed), _escape(status),
                _escape(_num(item.get("latency_ms"), 0, " ms")),
            )
        elif item.get("status") == "early_discard":
            right = "前段 %s<br>%s" % (
                _escape(_num(item.get("early_speed_mbps"), 1, " Mbps")),
                _escape(status),
            )
        elif item.get("status") == "incomplete":
            right = "%s<br>采样 %s" % (
                _escape(status),
                _escape(_num(item.get("sampled_seconds"), 1, " 秒")),
            )
        else:
            right = _escape(status)
        if item.get("selected"):
            right = "★ " + right
        rows.append((left, right))
    return rows


def _full_report(report, error):
    tiers = report.get("tiers_mbps") or []
    original_target = tiers[0] if tiers else None
    selected_tier = report.get("selected_tier_mbps")
    candidates = report.get("candidates") or []
    rtt_count = sum(row.get("latency_ms") is not None for row in candidates)
    attempted = sum(len(row.get("speed_test_attempts") or []) for row in candidates)
    assigned = report.get("regional_assignments") or {}
    applied = report.get("applied_nodes") or {}
    preserved = report.get("preserved_nodes") or []
    missing = report.get("nodes_missing_fallback_ip") or []
    met = bool(report.get("met_target_count_at_tier")) and not missing
    if error:
        header = "❌ CF IP 测速 / 更新异常"
        state = "异常（见原因）"
    elif met:
        header = "✅ X-Tunnel · CF IP 测速完成"
        state = "达标"
    else:
        header = "⚠️ X-Tunnel · CF IP 测速完成"
        state = "部分达标 / 不足"
    rows = [
        _row("本次状态", state),
        _row("候选 / RTT 有效", "%d / %d" % (len(candidates), rtt_count)),
        _row("下载测速", "%d 次" % attempted),
    ]
    if original_target is not None:
        rows.append(_row("目标速度", _num(original_target, 0, " Mbps")))
    if selected_tier is not None:
        rows.append(_row("最终档位", _num(selected_tier, 0, " Mbps")))
    rows.extend([
        _row("IP 选取", "%d 个" % len(report.get("selected_ips") or [])),
        _row("节点写入", "已更新 %d 个" % len(applied)
             if applied else "未执行" if report.get("apply_mode") == "disabled"
             else "未写入"),
    ])
    summary = _title(header) + _table(("指标", "结果"), rows)

    if assigned or preserved:
        node_rows = []
        for name, assignment in assigned.items():
            address = assignment.get("ip") or "—"
            colo = assignment.get("colo") or "—"
            lines = ["<code>%s</code> [%s]" % (_escape(address), _escape(colo))]
            fallbacks = assignment.get("fallback_ips") or []
            if fallbacks:
                lines.append("备用：" + "、".join(
                    "<code>%s</code>" % _escape(ip.get("ip"))
                    for ip in fallbacks
                ))
            if name in applied:
                lines.append("✅ 已写入")
            else:
                lines.append("保留 / 未写入")
            node_rows.append((_escape(name), "<br>".join(lines)))
        reasons = report.get("preserved_node_reasons") or {}
        for name in preserved:
            if name not in assigned:
                node_rows.append((
                    _escape(name),
                    "保留原 IP<br>" + _escape(reasons.get(name, "无可用匹配 IP")),
                ))
        summary += "<h4>节点分配与写入</h4>" + _table(
            ("节点", "主 IP / 备用 / 状态"), node_rows,
        )
    elif report.get("apply_mode") == "disabled":
        summary += _paragraph("本次选择 --no-apply：仅测速，不写入 YAML。")
    if missing:
        summary += _paragraph("备用 IP 不足：" + "、".join(map(str, missing)))
    if report.get("apply_error"):
        summary += _paragraph("写入失败：" + str(report["apply_error"]))
    if error:
        summary += _paragraph("异常：" + str(error))

    attempts = _attempt_rows(report, selected_tier)
    if not attempts:
        return [summary + _paragraph("暂无可展示的下载测速明细。")]
    pages = []
    for offset in range(0, len(attempts), MAX_ROWS_PER_PAGE):
        block = attempts[offset:offset + MAX_ROWS_PER_PAGE]
        if offset == 0:
            prefix = summary + "<h4>下载测速明细</h4>"
        else:
            prefix = _title(
                "📊 CF IP 测速明细（续 %d）" % (offset // MAX_ROWS_PER_PAGE + 1)
            )
        pages.append(prefix + _table(("IP / 机房", "均速 · RTT / 状态"), block))
    return pages


def rich_report_pages(report, error=None):
    """Return HTML pages for Telegram sendRichMessage rich_message.html.

    Every page uses native bordered tables (not legacy monospace pseudo-tables).
    """
    if not report:
        return [_title("❌ X-Tunnel CF IP 测速失败") +
                _table(("项目", "结果"), [
                    _row("原因", error or "未生成测速报告")
                ])]
    if report.get("mode") == "single-ip":
        return _single_report(report, error)
    return _full_report(report, error)
