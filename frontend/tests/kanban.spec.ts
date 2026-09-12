import { expect, test } from "@playwright/test";

test("unified board shows lifecycle without arbitrary status controls", async ({ page }) => {
  await page.route("**/kanban/", route => route.fulfill({contentType:"text/html; charset=utf-8",body:'<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="csrf-token" content="token"><link rel="stylesheet" href="/static/kanban/kanban.css"><div id="root"></div><script type="module" src="/static/kanban/kanban.js"></script>'}));
  let assigned = false;
  await page.route("**/api/leads/board/", route => route.fulfill({json:{
    columns:[{id:"new",label:"Новый"},{id:"in_progress",label:"В работе"},{id:"assigned",label:"Назначен"},{id:"completed",label:"Завершён"},{id:"paid",label:"Оплачен"}],
    leads:[{id:1,key:"order-1",kind:"order",status:assigned?"assigned":"in_progress",detail:assigned?"Ожидает принятия мастером":"Клиент согласился",client_name:"Иван",phone:"123",title:"Ремонт",service_name:"Ремонт",service_id:2,employee_id:assigned?7:null,employee_name:assigned?"Антон":null}],
    workers:[{id:7,name:"Антон",service_ids:[2]}],can_change:true,can_add:true,can_manage:false
  }}));
  await page.route("**/api/orders/1/assign/", route => {
    expect(route.request().postDataJSON()).toEqual({employee_id:7});
    expect(route.request().headers()["x-csrftoken"]).toBe("token");
    assigned=true; return route.fulfill({json:{id:1}});
  });
  await page.goto("/kanban/");
  await expect(page.locator('[data-status="in_progress"]')).toContainText("Клиент согласился");
  await expect(page.locator('[draggable="true"]')).toHaveCount(0);
  await page.getByLabel("Мастер для заказа 1").selectOption("7");
  await page.getByRole("button",{name:"Назначить мастера"}).click();
  await expect(page.locator('[data-status="assigned"]')).toContainText("Антон");
  await page.reload();
  await expect(page.locator('[data-status="assigned"]')).toContainText("Ожидает принятия мастером");
  await page.setViewportSize({width:390,height:844});
  await page.getByRole("searchbox").fill("Нет совпадений");
  await expect(page.locator('[data-card-key]')).toHaveCount(0);
});
