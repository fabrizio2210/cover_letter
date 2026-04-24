import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule, Router, ActivatedRoute } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { FeedbackService } from '../../core/services/feedback.service';
import { CoverLetter } from '../../shared/models/models';

@Component({
  selector: 'app-letter-editor',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule],
  templateUrl: './letter-editor.component.html',
  styleUrls: ['./letter-editor.component.css']
})
export class LetterEditorComponent implements OnInit {
  id: string | null = null;
  letter: CoverLetter | null = null;
  content = '';
  loading = false;
  saving = false;
  refining = false;
  sending = false;

  constructor(private route: ActivatedRoute, private http: HttpClient, private router: Router, private feedback: FeedbackService) {}

  ngOnInit(): void {
    this.id = this.route.snapshot.paramMap.get('id');
    if (!this.id) {
      this.feedback.showFeedback('Missing cover letter id', true);
      this.router.navigate(['/dashboard', 'cover-letters']);
      return;
    }
    this.fetchLetter();
  }

  fetchLetter(): void {
    if (!this.id) return;
    this.loading = true;
    this.http.get<CoverLetter>(`/api/cover-letters/${this.id}`).subscribe({
      next: (data: CoverLetter) => {
        this.letter = data;
        this.content = data?.cover_letter || '';
        this.loading = false;
      },
      error: () => {
        this.loading = false;
        this.feedback.showFeedback('Failed to load cover letter', true);
      }
    });
  }

  save(): void {
    if (!this.id) return;
    this.saving = true;
    this.http.put(`/api/cover-letters/${this.id}`, { content: this.content }).subscribe({
      next: () => {
        this.saving = false;
        this.feedback.showFeedback('Cover letter saved');
        this.fetchLetter();
      },
      error: () => {
        this.saving = false;
        this.feedback.showFeedback('Failed to save', true);
      }
    });
  }

  refine(): void {
    if (!this.id) return;
    const prompt = window.prompt('Enter refinement prompt');
    if (!prompt) return;
    this.refining = true;
    this.http.post(`/api/cover-letters/${this.id}/refine`, { prompt }).subscribe({
      next: () => {
        this.refining = false;
        this.feedback.showFeedback('Refinement queued');
      },
      error: () => {
        this.refining = false;
        this.feedback.showFeedback('Failed to queue refinement', true);
      }
    });
  }

  send(): void {
    if (!this.id) return;
    if (!confirm('Send this cover letter?')) return;
    this.sending = true;
    this.http.post(`/api/cover-letters/${this.id}/send`, {}).subscribe({
      next: () => {
        this.sending = false;
        this.feedback.showFeedback('Email queued successfully');
      },
      error: () => {
        this.sending = false;
        this.feedback.showFeedback('Failed to queue email', true);
      }
    });
  }

  delete(): void {
    if (!this.id) return;
    if (!confirm('Delete this cover letter?')) return;
    this.http.delete(`/api/cover-letters/${this.id}`).subscribe({
      next: () => {
        this.feedback.showFeedback('Cover letter deleted');
        this.router.navigate(['/dashboard', 'cover-letters']);
      },
      error: () => {
        this.feedback.showFeedback('Failed to delete', true);
      }
    });
  }
}
