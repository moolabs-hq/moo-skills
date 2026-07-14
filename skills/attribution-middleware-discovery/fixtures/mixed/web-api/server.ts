import express from "express";
const app = express();
app.use(attributionMiddleware);
const suppliedPath = buildPath();
app.post("/orders", handler);
app.delete(suppliedPath, handler);
const customer = req.headers.get("X-Moolabs-Customer");
