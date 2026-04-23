/// <reference types="jasmine" />

import { AppComponent } from './app.component';

describe('AppComponent', () => {
	it('should be instantiable', () => {
		const app = new AppComponent();
		expect(app).toBeTruthy();
	});
});
