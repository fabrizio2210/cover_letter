import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FieldsListComponent } from './fields-list.component';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [CommonModule, FieldsListComponent],
  template: `
    <section class="settings-shell">
      <h3>Settings</h3>
      <p class="is-size-6">Manage shared configuration and fields.</p>
      <app-fields-list></app-fields-list>
    </section>
  `
})
export class SettingsComponent {}
