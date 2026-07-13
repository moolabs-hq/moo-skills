package main

func main() {
    r := chi.NewRouter()
    r.Use(
        AttributionMiddleware,
    )
    r.Get(
        "/multiline",
        handler,
    )
}
