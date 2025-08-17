import { Component } from '@angular/core';
import { LoginComponent } from './login.component';

@Component({
  selector: 'app-root',
  template: '<app-login></app-login>',
  standalone: true,
  imports: [LoginComponent]
})
export class AppComponent {}
