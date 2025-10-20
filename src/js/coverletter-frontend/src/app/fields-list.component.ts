import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterModule } from '@angular/router';

@Component({
  selector: 'app-fields-list',
  standalone: true,
  imports: [CommonModule, RouterModule],
  template: `
    <section>
      <h3>Fields</h3>
      <p class="is-size-6">List of sectors (e.g. fashion, charities). Backend should return joined results (field + identities).</p>
      <!-- TODO: implement list, create, edit -->
    </section>
  `
})
export class FieldsListComponent {}
