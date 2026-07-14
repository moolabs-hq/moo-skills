package main

import "github.com/go-chi/chi/v5"

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
