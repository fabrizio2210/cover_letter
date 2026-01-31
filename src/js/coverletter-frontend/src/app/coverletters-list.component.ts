import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule, Router } from '@angular/router';
import { HttpClient, HttpHeaders, HttpClientModule } from '@angular/common/http';
import { FeedbackService } from './services/feedback.service';

interface CoverLetterSummary {
  _id?: string;
  coverLetter?: string;
  cover_letter?: string;
  createdAt?: string | number;
  recipientInfo?: Array<any>;
}

@Component({
  selector: 'app-coverletters-list',
  standalone: true,
  imports: [CommonModule, RouterModule, HttpClientModule],
  templateUrl: './coverletters-list.component.html',
  styleUrls: ['./coverletters-list.component.css']
})
export class CoverLettersListComponent implements OnInit {
  letters: CoverLetterSummary[] = [];
  loading = false;

  constructor(private http: HttpClient, private router: Router, private feedback: FeedbackService) {}

  ngOnInit(): void {
    this.fetchList();
  }

  private getAuthHeaders(): HttpHeaders | null {
    const token = localStorage.getItem('token');
    if (!token) {
      this.router.navigate(['/login']);
      return null;
    }
    return new HttpHeaders().set('Authorization', `Bearer ${token}`);
  }

  fetchList(): void {
    const headers = this.getAuthHeaders();
    if (!headers) return;
    this.loading = true;
    this.http.get<CoverLetterSummary[]>('/api/cover-letters', { headers }).subscribe({
      next: (data) => { this.letters = data || []; this.loading = false; },
      error: (err) => { this.loading = false; this.feedback.showFeedback('Failed to load cover letters', true); if (err.status === 401) this.router.navigate(['/login']); }
    });
  }

  viewLetter(id: string | undefined): void {
    if (!id) return;
    this.router.navigate(['/dashboard', 'cover-letters', id]);
  }

  snippet(letter: CoverLetterSummary): string {
    const content = letter.coverLetter || (letter as any).cover_letter || '';
    return content.length > 140 ? content.slice(0, 140) + '…' : content;
  }

  deleteLetter(id: string | undefined): void {
    if (!id) return;
    if (!confirm('Delete this cover letter?')) return;
    const headers = this.getAuthHeaders();
    if (!headers) return;
    this.http.delete(`/api/cover-letters/${id}`, { headers }).subscribe({
      next: () => { this.feedback.showFeedback('Cover letter deleted'); this.fetchList(); },
      error: (err) => { this.feedback.showFeedback('Failed to delete cover letter', true); if (err.status === 401) this.router.navigate(['/login']); }
    });
  }
}
