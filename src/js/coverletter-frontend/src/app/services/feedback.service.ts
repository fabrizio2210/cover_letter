import { Injectable } from '@angular/core';
import { Subject } from 'rxjs';
import { FeedbackMessage } from '../models/models';

@Injectable({
  providedIn: 'root'
})
export class FeedbackService {
  private feedbackSubject = new Subject<FeedbackMessage>();
  feedback$ = this.feedbackSubject.asObservable();

  showFeedback(message: string, isError = false): void {
    this.feedbackSubject.next({ message, isError });
  }

  clearFeedback(): void {
    this.feedbackSubject.next({ message: '', isError: false });
  }
}