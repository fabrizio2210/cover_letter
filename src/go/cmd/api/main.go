package main

import (
	"os"

	"coverletter/handlers"
	"coverletter/middleware"

	"github.com/gin-gonic/gin"
)

var jwtSecret = []byte("change_this_secret") // Should be set via env in production

func main() {
	if envSecret := os.Getenv("JWT_SECRET"); envSecret != "" {
		jwtSecret = []byte(envSecret)
	}
	r := gin.Default()

	r.POST("/api/login", handlers.Login(jwtSecret))

	auth := r.Group("/api")
	auth.Use(middleware.JWT(jwtSecret))
	{
		auth.GET("/recipients", handlers.GetRecipients)
	}

	r.Run(":8080")
}
