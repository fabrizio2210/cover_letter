import { Component } from '@angular/core';
import { LoginComponent } from './login.component';
import { HttpClientModule } from '@angular/common/http';

@Component({
  selector: 'app-root',
  template: '<app-login></app-login>',
  standalone: true,
  imports: [LoginComponent, HttpClientModule]
})
export class AppComponent {}
