import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';

@Component({
  selector: 'app-identities-list',
  standalone: true,
  imports: [CommonModule, RouterModule],
  template: `
    <section>
      <h3>Identities</h3>
      <p class="is-size-6">Personas (e.g. makeup artist) — each identity belongs to a field. Backend should return identity + field.</p>
      <!-- TODO: implement list, create, edit -->
    </section>
  `
})
export class IdentitiesListComponent {}
