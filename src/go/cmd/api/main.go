package main

import (
	"os"

	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/facade"
	"github.com/fabrizio2210/cover_letter/src/go/cmd/api/middleware"

	"github.com/gin-gonic/gin"
)

var jwtSecret = []byte("change_this_secret") // Should be set via env in production

func main() {
	if envSecret := os.Getenv("JWT_SECRET"); envSecret != "" {
		jwtSecret = []byte(envSecret)
	}

	adminJWTSecret := []byte(os.Getenv("ADMIN_JWT_SECRET"))

	r := gin.Default()

	r.POST("/api/login", facade.Login(jwtSecret))
	r.POST("/api/admin/login", facade.AdminLogin(adminJWTSecret))

	// Admin-only routes — require a JWT signed with ADMIN_JWT_SECRET and role == "admin".
	admin := r.Group("/api/admin")
	admin.Use(middleware.Admin(adminJWTSecret))
	{
		admin.GET("/fields", facade.GetFields)
		admin.POST("/fields", facade.CreateField)
		admin.PUT("/fields/:id", facade.UpdateField)
		admin.DELETE("/fields/:id", facade.DeleteField)
	}

	auth := r.Group("/api")
	auth.Use(middleware.JWT(jwtSecret))
	{
		auth.GET("/recipients", facade.GetRecipients)
		auth.POST("/recipients", facade.CreateRecipient)
		auth.DELETE("/recipients/:id", facade.DeleteRecipient)
		auth.PUT("/recipients/:id/description", facade.UpdateRecipientDescription)
		auth.PUT("/recipients/:id/name", facade.UpdateRecipientName)
		auth.PUT("/recipients/:id/company", facade.AssociateCompanyWithRecipient)
		auth.POST("/recipients/:id/generate-cover-letter", facade.GenerateCoverLetterForRecipient)

		auth.GET("/identities", facade.GetIdentities)
		auth.POST("/identities", facade.CreateIdentity)
		auth.DELETE("/identities/:id", facade.DeleteIdentity)
		auth.PUT("/identities/:id/description", facade.UpdateIdentityDescription)
		auth.PUT("/identities/:id/name", facade.UpdateIdentityName)
		auth.PUT("/identities/:id/signature", facade.UpdateIdentitySignature)
		auth.PUT("/identities/:id/roles", facade.UpdateIdentityRoles)
		auth.PUT("/identities/:id/preferences", facade.UpdateIdentityPreferences)
		auth.PUT("/identities/:id/field", facade.AssociateFieldWithIdentity)

		auth.GET("/fields", facade.GetFields)

		// Company CRUD endpoints
		auth.GET("/companies", facade.GetCompanies)
		auth.POST("/companies", facade.CreateCompany)
		auth.PUT("/companies/:id", facade.UpdateCompany)
		auth.PUT("/companies/:id/field", facade.AssociateFieldWithCompany)
		auth.DELETE("/companies/:id", facade.DeleteCompany)

		auth.GET("/cover-letters", facade.GetCoverLetters)
		auth.GET("/cover-letters/:id", facade.GetCoverLetter)
		auth.DELETE("/cover-letters/:id", facade.DeleteCoverLetter)
		auth.PUT("/cover-letters/:id", facade.UpdateCoverLetter)
		auth.POST("/cover-letters/:id/refine", facade.RefineCoverLetter)
		auth.POST("/cover-letters/:id/send", facade.SendCoverLetter)

		auth.GET("/job-descriptions", facade.GetJobDescriptions)
		auth.GET("/job-descriptions/stream", facade.StreamJobUpdates)
		auth.GET("/job-descriptions/:id", facade.GetJobDescription)
		auth.GET("/job-preference-scores", facade.GetJobPreferenceScores)
		auth.POST("/job-descriptions", facade.CreateJobDescription)
		auth.PUT("/job-descriptions/:id", facade.UpdateJobDescription)
		auth.DELETE("/job-descriptions/:id", facade.DeleteJobDescription)
		auth.POST("/job-descriptions/:id/score", facade.ScoreJobDescription)
		auth.POST("/job-descriptions/:id/check", facade.CheckJobDescription)

		auth.POST("/crawls", facade.TriggerCrawl)
		auth.GET("/crawls/active", facade.GetActiveCrawls)
		auth.GET("/crawls/activity-summary", facade.GetActivitySummary)
		auth.GET("/crawls/last-run/workflow-stats", facade.GetLastRunWorkflowStats)
		auth.GET("/crawls/workflow-cumulative-jobs", facade.GetWorkflowCumulativeJobs)
		auth.GET("/crawls/stream", facade.StreamCrawlProgress)
		auth.GET("/scoring/active", facade.GetActiveScoring)
		auth.GET("/scoring/stream", facade.StreamScoringProgress)
	}

	r.Run(":8080")
}
