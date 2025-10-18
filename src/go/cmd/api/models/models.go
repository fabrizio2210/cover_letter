package models

import "go.mongodb.org/mongo-driver/bson/primitive"

// Field represents a field of study or industry.
type Field struct {
	ID    primitive.ObjectID `bson:"_id,omitempty" json:"_id"`
	Field string             `bson:"field" json:"field"`
}

// Recipient represents a person or company to whom a cover letter is addressed.
type Recipient struct {
	ID          primitive.ObjectID `bson:"_id,omitempty" json:"_id"`
	Email       string             `bson:"email" json:"email"`
	Name        string             `bson:"name,omitempty" json:"name,omitempty"`
	Description string             `bson:"description,omitempty" json:"description,omitempty"`
	FieldID     primitive.ObjectID `bson:"field,omitempty" json:"-"` // Use `json:"-"` to hide from JSON output
	FieldInfo   []Field            `bson:"fieldInfo,omitempty" json:"fieldInfo,omitempty"`
}
