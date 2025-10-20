import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';

@Component({
  selector: 'app-coverletters-list',
  standalone: true,
  imports: [CommonModule, RouterModule],
  template: `
    <section>
      <h3>Cover Letters</h3>
      <p class="is-size-6">Cover letters are created per recipient and based on an identity. Consider exposing filters: recipient, identity, date.</p>
      <!-- TODO: implement list (preferably fetched with recipient and identity joined data) -->
    </section>
  `
})
export class CoverLettersListComponent {}
