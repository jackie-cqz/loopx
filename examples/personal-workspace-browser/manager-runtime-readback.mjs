import assert from "node:assert/strict";
import { resolve } from "node:path";
import { outputDir } from "./fixture.mjs";
import { openWorkspacePage } from "./scenario-context.mjs";

export const managerRuntimeReadbackScenario = {
  id: "manager-runtime-readback",
  async run({ browser, collectCoverage, url }) {
    const trusted = {
      schema_version: "manager_runtime_session_readback_v0",
      runtime_profile: "trusted_owner", configuration_revision: "sha256:session-fixture",
      status: "ready", sandbox: "danger-full-access", standing_grant: "machine_configuration",
      tool_classes: ["loopx_core", "filesystem", "shell"],
    };
    const context = await openWorkspacePage(browser, url, {
      collectCoverage,
      beforeGoto: async (api) => { api.managerSessionRuntime = trusted; },
    });
    const { page, api } = context;
    try {
      await page.getByRole("navigation", { name: "管家视图" })
        .getByRole("button", { name: /^(Chat|对话)$/ }).click();
      const details = page.locator(".personal-runtime-details");
      await details.getByText("运行环境", { exact: true }).click();
      await details.getByText("trusted_owner · danger-full-access", { exact: true }).waitFor();
      // The capabilities fixture remains restricted. A later capability fetch
      // must not replace the selected Session's actual host policy.
      await page.waitForTimeout(3500);
      assert.match(await details.innerText(), /trusted_owner · danger-full-access/u);
      await page.screenshot({ path: resolve(outputDir, "manager-runtime-session-trusted.png"),
        fullPage: false, animations: "disabled" });
      api.managerSessionRuntime = {
        ...trusted, runtime_profile: "restricted", sandbox: "read-only", standing_grant: "none",
        configuration_revision: "unavailable", status: "configuration_invalid", tool_classes: ["loopx_core"],
      };
      await details.getByText(/配置无效，已回退到 restricted · read-only/u).waitFor();
      assert.doesNotMatch(await details.innerText(), /trusted_owner/u);
      await page.setViewportSize({ width: 390, height: 844 });
      if (await details.getAttribute("open") === null) {
        await details.getByText("运行环境", { exact: true }).click();
      }
      await details.getByText(/配置无效，已回退到 restricted · read-only/u).waitFor();
      await page.screenshot({ path: resolve(outputDir, "manager-runtime-session-fallback-mobile.png"),
        fullPage: false, animations: "disabled" });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), true);
      assert.equal(context.errors.length, 0);
      return { coverageEntries: context.coverageEntries,
        note: "Active Session policy wins over configuration, refreshes after downgrade, and renders fallback on mobile." };
    } finally { await context.close(); }
  },
};
