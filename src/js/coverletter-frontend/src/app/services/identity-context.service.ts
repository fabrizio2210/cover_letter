import { Injectable } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

@Injectable({
  providedIn: 'root'
})
export class IdentityContextService {
  private readonly storageKey = 'curator_selected_identity_id';
  private readonly selectedIdentityIdSubject = new BehaviorSubject<string>(this.readPersistedIdentityId());

  readonly selectedIdentityId$ = this.selectedIdentityIdSubject.asObservable();

  getSelectedIdentityId(): string {
    return this.selectedIdentityIdSubject.value;
  }

  setSelectedIdentityId(identityId: string): void {
    const normalizedIdentityId = (identityId || '').trim();
    this.selectedIdentityIdSubject.next(normalizedIdentityId);

    try {
      if (normalizedIdentityId) {
        localStorage.setItem(this.storageKey, normalizedIdentityId);
        return;
      }

      localStorage.removeItem(this.storageKey);
    } catch {
      // Ignore storage failures and keep in-memory state alive.
    }
  }

  ensureValidIdentityId(availableIdentityIds: string[], fallbackIdentityId = ''): string {
    const currentIdentityId = this.getSelectedIdentityId();
    if (currentIdentityId && availableIdentityIds.includes(currentIdentityId)) {
      return currentIdentityId;
    }

    const normalizedFallback = (fallbackIdentityId || '').trim();
    const resolvedIdentityId = availableIdentityIds.includes(normalizedFallback)
      ? normalizedFallback
      : (availableIdentityIds[0] || '');

    this.setSelectedIdentityId(resolvedIdentityId);
    return resolvedIdentityId;
  }

  private readPersistedIdentityId(): string {
    try {
      return (localStorage.getItem(this.storageKey) || '').trim();
    } catch {
      return '';
    }
  }
}