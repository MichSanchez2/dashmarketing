let
    baseUrl = "https://dashmarketing.onrender.com/exportar",
    fromDate = Date.ToText(Date.AddDays(DateTimeZone.UtcNow(), -30), "yyyy-MM-dd"),
    toDate = Date.ToText(DateTimeZone.UtcNow(), "yyyy-MM-dd"),
    url = baseUrl & "?from=" & fromDate & "&to=" & toDate & "&pageSize=1000",
    source = Web.Contents(url, [Timeout=#duration(0,0,2,0)]),
    json = Json.Document(source),
    partial = if Record.HasFields(json, "partial") then json[partial] else true,
    _ = if partial then error "partial response" else null,
    rows = Table.FromList(json[rows], Splitter.SplitByNothing(), null, null, ExtraValues.Error)
in
    rows
