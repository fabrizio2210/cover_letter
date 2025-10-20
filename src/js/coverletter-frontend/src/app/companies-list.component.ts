import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';

@Component({
  selector: 'app-companies-list',
  standalone: true,
  imports: [CommonModule, RouterModule],
  template: `
    <section>
      <h3>Companies</h3>
      <p class="is-size-6">Companies belong to a Field. Recipients belong to a Company. Backend should support CRUD and return company + field joins.</p>
      <!-- TODO: implement list, inline edit, create -->
    </section>
  `
})
export class CompaniesListComponent {}
