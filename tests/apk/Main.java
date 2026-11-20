package dev.cuttlefish.cfctl.fixture;

import android.app.Activity;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.view.Gravity;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;

public class Main extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        SharedPreferences prefs = getSharedPreferences("test", MODE_PRIVATE);
        Button button = new Button(this);
        button.setText(prefs.getBoolean("clicked", false) ? "VERIFIED" : "START");
        button.setOnClickListener(view -> {
            prefs.edit().putBoolean("clicked", true).commit();
            button.setText("VERIFIED");
        });
        EditText field = new EditText(this);
        field.setSingleLine(true);
        field.setText("initial");
        field.setContentDescription("Fixture input");
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setGravity(Gravity.CENTER_VERTICAL);
        layout.addView(button);
        layout.addView(field);
        setContentView(layout);
    }
}
