import { Hono } from "hono";
const api = new Hono();
app.use("/v1", api);
api.get("/hono", handler);
