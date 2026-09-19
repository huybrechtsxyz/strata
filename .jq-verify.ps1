$jq = "$env:TEMP\jq-verify\jq.exe"
$filter = '(.data.artifact_diff // [] | map(select(.status != "unchanged")) | length > 0) or (.data.terraform_plan // [] | map(.has_changes == true) | any)'

$cases = [ordered]@{
  'all stages unchanged'       = @{ json = '{"data":{"artifact_diff":[{"status":"unchanged"}],"terraform_plan":[{"has_changes":false},{"has_changes":false}]}}'; expect = 'false' }
  'one stage has changes'      = @{ json = '{"data":{"artifact_diff":[{"status":"unchanged"}],"terraform_plan":[{"has_changes":false},{"has_changes":true}]}}'; expect = 'true' }
  'artifacts changed only'     = @{ json = '{"data":{"artifact_diff":[{"status":"changed"}],"terraform_plan":[{"has_changes":false}]}}'; expect = 'true' }
  'artifacts-only mode'        = @{ json = '{"data":{"artifact_diff":[{"status":"unchanged"}],"terraform_plan":[]}}'; expect = 'false' }
  'has_changes null'           = @{ json = '{"data":{"artifact_diff":[{"status":"unchanged"}],"terraform_plan":[{"has_changes":null}]}}'; expect = 'false' }
  'stage skipped (no plan)'    = @{ json = '{"data":{"artifact_diff":[],"terraform_plan":[{"would_skip":true,"has_changes":null}]}}'; expect = 'false' }
  'keys absent entirely'       = @{ json = '{"data":{}}'; expect = 'false' }
  'OLD BUG: rows but no change'= @{ json = '{"data":{"artifact_diff":[{"status":"unchanged"}],"terraform_plan":[{"has_changes":false}]}}'; expect = 'false' }
}

$fail = 0
foreach ($name in $cases.Keys) {
  $c = $cases[$name]
  $actual = ($c.json | & $jq -r $filter) 2>&1
  $status = if ("$actual" -eq $c.expect) { 'PASS' } else { $fail++; 'FAIL' }
  "{0}  {1,-30} expect={2,-5} actual={3}" -f $status, $name, $c.expect, $actual
}
""
if ($fail -eq 0) { "ALL $($cases.Count) CASES PASS" } else { "$fail FAILURE(S)" }
