import { Component } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FieldsListComponent } from './fields-list.component';

@Component({
  selector: 'app-settings',
  standalone: true,
  imports: [CommonModule, FieldsListComponent],
  template: `
    <section class="settings-shell">
      <app-fields-list />
    </section>
  `
})
export class SettingsComponent {}
