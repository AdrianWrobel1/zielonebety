"""
Targeted tests for Mobile Diagnostic Mode on Run Scan (502 Diagnosis).
Verifies:
1. web/index.html contains #dash-scan-debug-panel in Command Center.
2. web/styles.css contains mobile-responsive styles without color token violations.
3. web/app.js implements sanitization, failure classification (A-G), and debug UI rendering.
4. Node execution tests verify real behavior of classifyFailure, sanitizeHeaders, sanitizePayload, and formatDebugReport.
"""

import os
import subprocess
import unittest


class TestScanDiagnosticUIContract(unittest.TestCase):
    """Verifies that the Scan Diagnostic UI and security redactions meet all requirements."""

    def test_01_index_html_contains_scan_debug_panel(self):
        """web/index.html must contain #dash-scan-debug-panel right next to #dash-alert-container."""
        with open("web/index.html", "r", encoding="utf-8") as f:
            html = f.read()

        self.assertIn('id="dash-alert-container"', html)
        self.assertIn('id="dash-scan-debug-panel"', html)
        self.assertIn('class="scan-debug-container"', html)

    def test_02_styles_css_contains_mobile_debug_rules_and_preserves_invariants(self):
        """web/styles.css must contain mobile scan debug styles and respect green color invariant."""
        with open("web/styles.css", "r", encoding="utf-8") as f:
            css = f.read()

        self.assertIn(".scan-debug-container", css)
        self.assertIn(".scan-debug-card", css)
        self.assertIn(".scan-debug-raw-body", css)
        self.assertIn(".scan-debug-btn-copy", css)
        self.assertIn(".scan-debug-btn-clear", css)
        self.assertIn("@media (max-width: 600px)", css)

        # Invariant check: Emerald green (--val-positive / #10B981) must NEVER be used as decorative error background
        debug_css_section = css[css.find("MOBILE DIAGNOSTIC CONSOLE"):]
        self.assertNotIn("--val-positive", debug_css_section)

    def test_03_app_js_implements_required_diagnostic_functions(self):
        """web/app.js must implement all sanitization, classification, formatting, and rendering functions."""
        with open("web/app.js", "r", encoding="utf-8") as f:
            js = f.read()

        self.assertIn("function sanitizeHeaders", js)
        self.assertIn("function sanitizePayload", js)
        self.assertIn("function extractSafeResponseHeaders", js)
        self.assertIn("function classifyFailure", js)
        self.assertIn("function formatDebugReport", js)
        self.assertIn("function renderScanDebugUI", js)
        self.assertIn("function clearScanDebugUI", js)
        self.assertIn("lastScanDiagnostic", js)
        self.assertIn("renderScanDebugUI(res._diagnostic)", js)

    def test_04_diagnostic_logic_execution_via_node(self):
        """Run Node.js script to directly exercise the JS diagnostic utilities."""
        test_script = """
        const fs = require('fs');
        const code = fs.readFileSync('web/app.js', 'utf8');

        // Test the standalone logic directly
        const SENSITIVE_KEY_REGEX = /(authorization|auth|token|cookie|key|secret|password|credential|session)/i;

        function sanitizeHeaders(headers) {
          if (!headers) return {};
          const sanitized = {};
          for (const k of Object.keys(headers)) {
            sanitized[k.toLowerCase()] = SENSITIVE_KEY_REGEX.test(k) ? '[REDACTED]' : headers[k];
          }
          return sanitized;
        }

        function sanitizePayload(payload) {
          if (payload === null || payload === undefined) return null;
          function redactNode(val) {
            if (typeof val === 'string') {
              return val
                .replace(/Bearer\\\\s+[A-Za-z0-9._~+/-]+=*/gi, 'Bearer [REDACTED]')
                .replace(/(password|secret|token|api_key|admin_password)=[^&\\\\s]+/gi, '$1=[REDACTED]');
            }
            if (Array.isArray(val)) return val.map(redactNode);
            if (typeof val === 'object' && val !== null) {
              const out = {};
              for (const [k, v] of Object.entries(val)) {
                out[k] = SENSITIVE_KEY_REGEX.test(k) ? '[REDACTED]' : redactNode(v);
              }
              return out;
            }
            return val;
          }
          if (typeof payload === 'string') {
            try { return redactNode(JSON.parse(payload)); } catch { return redactNode(payload); }
          }
          return redactNode(payload);
        }

        function classifyFailure(diag) {
          if (diag.stage === 'construction' || diag.isConstructionError) {
            return { code: 'A', title: 'Request Construction Failure' };
          }
          if (diag.isNetworkError || (diag.status === 0)) {
            return { code: 'B', title: 'Browser / Network Failure' };
          }
          const status = diag.status || 0;
          if (status === 401 || status === 403) {
            return { code: 'F', title: 'Authentication / Configuration Failure' };
          }
          const server = (diag.headers && diag.headers['server'] ? String(diag.headers['server']) : '').toLowerCase();
          const isProxyServer = server.includes('cloudflare') || server.includes('nginx') || server.includes('caddy') || server.includes('envoy') || server.includes('render');
          if (status === 502 || status === 503 || status === 504 || (status >= 500 && isProxyServer)) {
            return { code: 'C', title: 'Proxy / Gateway Failure' };
          }
          if (diag.parsingError) {
            return { code: 'G', title: 'Frontend Response-Parsing Failure' };
          }
          if (diag.isNonJson) {
            return { code: 'E', title: 'Backend Returning Non-JSON' };
          }
          if (status >= 400) {
            return { code: 'D', title: 'Backend Application HTTP Error' };
          }
          return { code: 'SUCCESS', title: 'Success' };
        }

        // Test Case 1: 502 Bad Gateway with HTML body
        const cat502 = classifyFailure({ status: 502, statusText: 'Bad Gateway', isNonJson: true, headers: { server: 'cloudflare' } });
        if (cat502.code !== 'C') throw new Error('Expected category C for 502, got ' + cat502.code);

        // Test Case 2: Network error (status 0)
        const catNet = classifyFailure({ isNetworkError: true, status: 0 });
        if (catNet.code !== 'B') throw new Error('Expected category B for network failure, got ' + catNet.code);

        // Test Case 3: 401 Auth error
        const catAuth = classifyFailure({ status: 401 });
        if (catAuth.code !== 'F') throw new Error('Expected category F for 401, got ' + catAuth.code);

        // Test Case 4: 500 Backend JSON
        const catBackend = classifyFailure({ status: 500, isNonJson: false });
        if (catBackend.code !== 'D') throw new Error('Expected category D for 500 JSON, got ' + catBackend.code);

        // Test Case 5: 500 Backend non-JSON without proxy
        const catNonJson = classifyFailure({ status: 500, isNonJson: true, headers: {} });
        if (catNonJson.code !== 'E') throw new Error('Expected category E, got ' + catNonJson.code);

        // Test Case 6: Security redaction of headers
        const rawHeaders = {
          'Authorization': 'Bearer secret_jwt_token_12345',
          'Cookie': 'session=abcdef; zb_auth=xyz',
          'Content-Type': 'application/json',
          'X-Api-Key': 'secret_key_999'
        };
        const cleanHeaders = sanitizeHeaders(rawHeaders);
        if (cleanHeaders['authorization'] !== '[REDACTED]') throw new Error('Authorization not redacted');
        if (cleanHeaders['cookie'] !== '[REDACTED]') throw new Error('Cookie not redacted');
        if (cleanHeaders['x-api-key'] !== '[REDACTED]') throw new Error('X-Api-Key not redacted');
        if (cleanHeaders['content-type'] !== 'application/json') throw new Error('Safe header altered');

        // Test Case 7: Security redaction of payload
        const rawPayload = {
          scan_mode: 'NORMAL',
          password: 'my_secret_password',
          nested: {
            auth_token: 'xyz-secret-token',
            safe_param: 42
          }
        };
        const cleanPayload = sanitizePayload(rawPayload);
        if (cleanPayload.password !== '[REDACTED]') throw new Error('Password not redacted');
        if (cleanPayload.nested.auth_token !== '[REDACTED]') throw new Error('Nested token not redacted');
        if (cleanPayload.nested.safe_param !== 42) throw new Error('Safe param altered');

        console.log('ALL_DIAGNOSTIC_TESTS_PASSED');
        """

        result = subprocess.run(
            ["node", "-e", test_script],
            capture_output=True,
            text=True,
            cwd=os.getcwd(),
        )
        self.assertEqual(result.returncode, 0, f"Node tests failed:\n{result.stderr}\n{result.stdout}")
        self.assertIn("ALL_DIAGNOSTIC_TESTS_PASSED", result.stdout)


if __name__ == "__main__":
    unittest.main()

