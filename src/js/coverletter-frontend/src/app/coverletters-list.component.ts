import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule, Router } from '@angular/router';
import { HttpClient } from '@angular/common/http';
import { FeedbackService } from './services/feedback.service';
import { CoverLetter, Timestamp } from './models/models';

@Component({
  selector: 'app-coverletters-list',
  standalone: true,
  imports: [CommonModule, RouterModule],
  templateUrl: './coverletters-list.component.html',
  styleUrls: ['./coverletters-list.component.css']
})
export class CoverLettersListComponent implements OnInit {
  letters: CoverLetter[] = [];
  loading = false;

  constructor(private http: HttpClient, private router: Router, private feedback: FeedbackService) {}

  ngOnInit(): void {
    this.fetchList();
  }

  fetchList(): void {
    this.loading = true;
    this.http.get<CoverLetter[]>('/api/cover-letters').subscribe({
      next: (data) => {
        const list = (data || []) as CoverLetter[];
        this.letters = list.map((d) => {
          if (d && d.created_at && typeof d.created_at === 'object' && 'seconds' in (d.created_at as any)) {
            const secs = Number((d.created_at as any).seconds) || 0;
            const nanos = Number((d.created_at as any).nanos) || 0;
            const ms = secs * 1000 + Math.floor(nanos / 1e6);
            return { ...d, created_at: new Date(ms).toISOString() };
          }
          return d;
        });
        this.loading = false;
      },
      error: () => { this.loading = false; this.feedback.showFeedback('Failed to load cover letters', true); }
    });
  }

  viewLetter(id: string | undefined): void {
    if (!id) return;
    this.router.navigate(['/dashboard', 'cover-letters', id]);
  }

  snippet(letter: CoverLetter): string {
    const content = letter.cover_letter || '';
    return content.length > 140 ? content.slice(0, 140) + '…' : content;
  }

  deleteLetter(id: string | undefined): void {
    if (!id) return;
    if (!confirm('Delete this cover letter?')) return;
    this.http.delete(`/api/cover-letters/${id}`).subscribe({
      next: () => { this.feedback.showFeedback('Cover letter deleted'); this.fetchList(); },
      error: () => { this.feedback.showFeedback('Failed to delete cover letter', true); }
    });
  }

  formatCreatedAt(value: string | number | Timestamp | undefined): string {
    if (!value) return '';
    if (typeof value === 'string' || typeof value === 'number') {
      const d = new Date(value as any);
      if (isNaN(d.getTime())) return String(value);
      return d.toLocaleString();
    }
    // protobuf Timestamp object
    const seconds = Number((value as any).seconds) || 0;
    const nanos = Number((value as any).nanos) || 0;
    const ms = seconds * 1000 + Math.floor(nanos / 1e6);
    return new Date(ms).toLocaleString();
  }
}
