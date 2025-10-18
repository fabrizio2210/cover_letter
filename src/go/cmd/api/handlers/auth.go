package handlers

import (
	"net/http"
	"os"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/golang-jwt/jwt/v5"
)

// Login handles the login request.
func Login(jwtSecret []byte) gin.HandlerFunc {
	return func(c *gin.Context) {
		var req struct {
			Password string `json:"password"`
		}
		if err := c.ShouldBindJSON(&req); err != nil {
			c.JSON(http.StatusBadRequest, gin.H{"error": "Invalid request"})
			return
		}
		adminPass := os.Getenv("ADMIN_PASSWORD")
		if adminPass == "" || req.Password != adminPass {
			c.JSON(http.StatusUnauthorized, gin.H{"error": "Unauthorized"})
			return
		}
		token := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.MapClaims{
			"exp": time.Now().Add(time.Hour * 24).Unix(),
		})
		tokenString, err := token.SignedString(jwtSecret)
		if err != nil {
			c.JSON(http.StatusInternalServerError, gin.H{"error": "Token error"})
			return
		}
		c.JSON(http.StatusOK, gin.H{"token": tokenString})
	}
}
