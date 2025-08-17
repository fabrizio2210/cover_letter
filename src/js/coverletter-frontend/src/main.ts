import { bootstrapApplication } from '@angular/platform-browser';
import { AppComponent } from './app/app.component';


const bootstrapApp: typeof bootstrapApplication = bootstrapApplication;

bootstrapApp(AppComponent)
.catch((err: unknown): void => console.error(err));

interface ErrorHandler {
  (err: unknown): void;
}

const typedBootstrapApplication: typeof bootstrapApplication = bootstrapApplication;
const typedErrorHandler: ErrorHandler = (err) => console.error(err);

typedBootstrapApplication(AppComponent)
  .catch(typedErrorHandler);
