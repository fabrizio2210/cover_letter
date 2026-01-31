import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterModule, Router, ActivatedRoute } from '@angular/router';
import { HttpClient, HttpHeaders, HttpClientModule } from '@angular/common/http';
import { FeedbackService } from './services/feedback.service';

@Component({
  selector: 'app-coverletters-detail',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterModule, HttpClientModule],
  templateUrl: './coverletters-detail.component.html',
  styleUrls: ['./coverletters-detail.component.css']
})
export class CoverLettersDetailComponent implements OnInit {
  id: string | null = null;
  letter: any = null;
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

  private getAuthHeaders(): HttpHeaders | null {
    const token = localStorage.getItem('token');
    if (!token) {
      this.router.navigate(['/login']);
      return null;
    }
    return new HttpHeaders().set('Authorization', `Bearer ${token}`);
  }

  fetchLetter(): void {
    const headers = this.getAuthHeaders();
    if (!headers || !this.id) return;
    this.loading = true;
    this.http.get<any>(`/api/cover-letters/${this.id}`, { headers }).subscribe({
      next: (data) => {
        this.letter = data;
        this.content = data?.coverLetter || data?.cover_letter || '';
        this.loading = false;
      },
      error: (err) => {
        this.loading = false;
        this.feedback.showFeedback('Failed to load cover letter', true);
        if (err.status === 401) this.router.navigate(['/login']);
      }
    });
  }

  save(): void {
    if (!this.id) return;
    const headers = this.getAuthHeaders();
    if (!headers) return;
    this.saving = true;
    this.http.put(`/api/cover-letters/${this.id}`, { content: this.content }, { headers }).subscribe({
      next: () => { this.saving = false; this.feedback.showFeedback('Cover letter saved'); this.fetchLetter(); },
      error: (err) => { this.saving = false; this.feedback.showFeedback('Failed to save', true); if (err.status === 401) this.router.navigate(['/login']); }
    });
  }

  refine(): void {
    if (!this.id) return;
    const prompt = window.prompt('Enter refinement prompt');
    if (!prompt) return;
    const headers = this.getAuthHeaders();
    if (!headers) return;
    this.refining = true;
    this.http.post(`/api/cover-letters/${this.id}/refine`, { prompt }, { headers }).subscribe({
      next: () => { this.refining = false; this.feedback.showFeedback('Refinement queued'); },
      error: (err) => { this.refining = false; this.feedback.showFeedback('Failed to queue refinement', true); if (err.status === 401) this.router.navigate(['/login']); }
    });
  }

  send(): void {
    if (!this.id) return;
    if (!confirm('Send this cover letter?')) return;
    const headers = this.getAuthHeaders();
    if (!headers) return;
    this.sending = true;
    this.http.post(`/api/cover-letters/${this.id}/send`, {}, { headers }).subscribe({
      next: () => { this.sending = false; this.feedback.showFeedback('Email queued successfully'); },
      error: (err) => { this.sending = false; this.feedback.showFeedback('Failed to queue email', true); if (err.status === 401) this.router.navigate(['/login']); }
    });
  }

  delete(): void {
    if (!this.id) return;
    if (!confirm('Delete this cover letter?')) return;
    const headers = this.getAuthHeaders();
    if (!headers) return;
    this.http.delete(`/api/cover-letters/${this.id}`, { headers }).subscribe({
      next: () => { this.feedback.showFeedback('Cover letter deleted'); this.router.navigate(['/dashboard', 'cover-letters']); },
      error: (err) => { this.feedback.showFeedback('Failed to delete', true); if (err.status === 401) this.router.navigate(['/login']); }
    });
  }
}
