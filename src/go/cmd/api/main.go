package main

import (
	"os"

	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/handlers"
	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/middleware"

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
		auth.POST("/recipients", handlers.CreateRecipient)
		auth.DELETE("/recipients/:id", handlers.DeleteRecipient)
		auth.PUT("/recipients/:id/description", handlers.UpdateRecipientDescription)
		auth.PUT("/recipients/:id/name", handlers.UpdateRecipientName)
		auth.PUT("/recipients/:id/company", handlers.AssociateCompanyWithRecipient)
		auth.POST("/recipients/:id/generate-cover-letter", handlers.GenerateCoverLetterForRecipient)

		auth.GET("/identities", handlers.GetIdentities)
		auth.POST("/identities", handlers.CreateIdentity)
		auth.DELETE("/identities/:id", handlers.DeleteIdentity)
		auth.PUT("/identities/:id/description", handlers.UpdateIdentityDescription)
		auth.PUT("/identities/:id/name", handlers.UpdateIdentityName)
		auth.PUT("/identities/:id/signature", handlers.UpdateIdentitySignature)
		auth.PUT("/identities/:id/roles", handlers.UpdateIdentityRoles)
		auth.PUT("/identities/:id/preferences", handlers.UpdateIdentityPreferences)
		auth.PUT("/identities/:id/field", handlers.AssociateFieldWithIdentity)

		auth.GET("/fields", handlers.GetFields)
		auth.POST("/fields", handlers.CreateField)
		auth.PUT("/fields/:id", handlers.UpdateField)
		auth.DELETE("/fields/:id", handlers.DeleteField)

		// Company CRUD endpoints
		auth.GET("/companies", handlers.GetCompanies)
		auth.POST("/companies", handlers.CreateCompany)
		auth.PUT("/companies/:id", handlers.UpdateCompany)
		auth.PUT("/companies/:id/field", handlers.AssociateFieldWithCompany)
		auth.DELETE("/companies/:id", handlers.DeleteCompany)

		auth.GET("/cover-letters", handlers.GetCoverLetters)
		auth.GET("/cover-letters/:id", handlers.GetCoverLetter)
		auth.DELETE("/cover-letters/:id", handlers.DeleteCoverLetter)
		auth.PUT("/cover-letters/:id", handlers.UpdateCoverLetter)
		auth.POST("/cover-letters/:id/refine", handlers.RefineCoverLetter)
		auth.POST("/cover-letters/:id/send", handlers.SendCoverLetter)

		auth.GET("/job-descriptions", handlers.GetJobDescriptions)
		auth.GET("/job-descriptions/:id", handlers.GetJobDescription)
		auth.POST("/job-descriptions", handlers.CreateJobDescription)
		auth.PUT("/job-descriptions/:id", handlers.UpdateJobDescription)
		auth.DELETE("/job-descriptions/:id", handlers.DeleteJobDescription)
		auth.POST("/job-descriptions/:id/score", handlers.ScoreJobDescription)

		auth.POST("/crawls", handlers.TriggerCrawl)
		auth.GET("/crawls/active", handlers.GetActiveCrawls)
		auth.GET("/crawls/stream", handlers.StreamCrawlProgress)
		auth.GET("/scoring/active", handlers.GetActiveScoring)
		auth.GET("/scoring/stream", handlers.StreamScoringProgress)
	}

	r.Run(":8080")
}
