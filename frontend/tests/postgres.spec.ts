import { execFileSync } from "node:child_process";
import { expect, test } from "@playwright/test";

test("consent and assignment persist on unified PostgreSQL board", async ({ page, context }) => {
  test.skip(process.env.RUN_POSTGRES_E2E !== "1", "Explicit PostgreSQL opt-in.");
  const python = "../.venv/Scripts/python.exe";
  const helper = "tests/postgres_fixture.py";
  const fixture = JSON.parse(execFileSync(python, [helper, "create", "unassigned"], { encoding: "utf8" }));
  try {
    await context.addCookies([{name:"sessionid",value:fixture.session,url:"http://127.0.0.1:8765"}]);
    await page.goto("/kanban/");
    await page.locator(`[data-card-key="lead-${fixture.lead_id}"]`).getByRole("button", {name:"Клиент согласился"}).click();
    const card = page.locator('[data-status="in_progress"] article').filter({hasText:fixture.service_id ? "Kanban E2E" : ""});
    await expect(card).toHaveCount(1);
    await card.getByRole("combobox").selectOption(String(fixture.employee_id));
    await card.getByRole("button",{name:"Назначить мастера"}).click();
    await page.reload();
    await expect(page.locator('[data-status="assigned"] article').filter({hasText:fixture.username})).toHaveCount(1);
    expect(execFileSync(python,[helper,"status"],{input:JSON.stringify(fixture),encoding:"utf8"}).trim()).toBe("converted");
  } finally {
    execFileSync(python,[helper,"cleanup"],{input:JSON.stringify(fixture),encoding:"utf8"});
  }
});

test("role pages isolate workers and reserve payment for manager", async ({ page, context }) => {
  test.skip(process.env.RUN_POSTGRES_E2E !== "1", "Explicit PostgreSQL opt-in.");
  const python = "../.venv/Scripts/python.exe";
  const helper = "tests/postgres_fixture.py";
  const fixture = JSON.parse(execFileSync(python, [helper, "create", "roles"], { encoding: "utf8" }));
  async function login(session: string) {
    await context.clearCookies();
    await context.addCookies([{ name: "sessionid", value: session, url: "http://127.0.0.1:8765" }]);
    await page.goto("/");
  }
  try {
    await login(fixture.worker_session);
    await expect(page).toHaveURL(/my-orders/);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole("heading", { name: "Мои назначения" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await page.screenshot({ path: "test-results/worker-mobile.png", fullPage: true });
    await page.setViewportSize({ width: 1440, height: 960 });
    await page.goto(`/orders/${fixture.order_id}/`);
    await page.getByRole("button", { name: "Принять / начать работу" }).click();
    await page.getByRole("button", { name: "Завершить", exact: true }).click();
    await expect(page.getByRole("button", { name: "Подтвердить получение оплаты" })).toHaveCount(0);
    expect((await page.request.get("/api/leads/board/")).status()).toBe(403);
    await page.screenshot({ path: "test-results/worker-page.png", fullPage: true });
    await login(fixture.session);
    await expect(page).toHaveURL(/operator/);
    await page.goto(`/orders/${fixture.order_id}/`);
    const csrf = await page.locator('input[name="csrfmiddlewaretoken"]').first().inputValue();
    expect((await page.request.post(`/orders/${fixture.order_id}/`, { form: { action: "pay", csrfmiddlewaretoken: csrf } })).status()).toBe(403);
    await login(fixture.manager_session);
    await expect(page).toHaveURL(/manager/);
    await page.screenshot({ path: "test-results/manager-page.png", fullPage: true });
    await page.goto(`/orders/${fixture.order_id}/`);
    await page.getByRole("button", { name: "Подтвердить получение оплаты" }).click();
    await expect(page.getByText("Оплачен", { exact: true })).toBeVisible();
  } finally {
    execFileSync(python, [helper, "cleanup"], { input: JSON.stringify(fixture), encoding: "utf8" });
  }
});
