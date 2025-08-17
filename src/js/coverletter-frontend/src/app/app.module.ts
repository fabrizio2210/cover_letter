import { NgModule } from '@angular/core';
import { BrowserModule } from '@angular/platform-browser';
import { FormsModule } from '@angular/forms';
import { HttpClientModule } from '@angular/common/http';

import { AppComponent } from './app.component';
import { LoginComponent } from './login.component';

@NgModule({
  declarations: [
    // AppComponent is standalone, so do not declare it here
  ],
  imports: [
    BrowserModule,
    FormsModule,
    HttpClientModule,
    // Import AppComponent as a standalone component
    AppComponent,
    // Import LoginComponent as a standalone component if needed
    LoginComponent
  ],
  providers: [],
  // Remove bootstrap array, use bootstrapApplication in main.ts instead
})
export class AppModule { }