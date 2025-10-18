import { Component, OnInit, inject } from '@angular/core';
import { CommonModule } from '@angular/common';
import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Router } from '@angular/router';

// Define an interface for the recipient data
export interface Recipient {
  _id: string;
  email: string;
  name?: string;
  description?: string;
  fieldInfo?: {
    _id: string;
    field: string;
  };
}

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './dashboard.component.html',
})
export class DashboardComponent implements OnInit {
  private http = inject(HttpClient);
  private router = inject(Router);

  recipients: Recipient[] = [];
  error = '';

  ngOnInit(): void {
    this.getRecipients();
  }

  getRecipients(): void {
    const token = localStorage.getItem('token');
    if (!token) {
      this.router.navigate(['/login']);
      return;
    }

    const headers = new HttpHeaders().set('Authorization', `Bearer ${token}`);

    this.http.get<Recipient[]>('/api/recipients', { headers }).subscribe({
      next: (data) => {
        this.recipients = data;
      },
      error: (err) => {
        this.error = 'Failed to fetch recipients.';
        console.error(err);
        if (err.status === 401) {
          this.router.navigate(['/login']);
        }
      },
    });
  }
}