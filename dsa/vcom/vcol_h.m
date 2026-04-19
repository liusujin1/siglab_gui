% vcol_h.m                                                   
% Dick Benson, DSP Technology
% Nov 17,1998
% color index definitions
     FIG_BKc  = 1;                  % Figure Background 
     DLG_BKc  = 2;                  % Dialog Background 
     LBL_BKc  = 3;                  % Label Background 
     EDT_BKc  = 4;                  % Edit Background 
     PLT_BKc  = 5;                  % Plot Background
     SPAREc   = 6;                  % Spare color 
     TRA_1c   = 7;                  % Plot trace 1 color
     TRA_2c   = 8;                  % Plot trace 2 color
     TRA_3c   = 9;                  % Plot trace 3 color
     TRA_4c   =10;                  % Plot trace 4 color   
     PA_1c    =11;                  % Patch1 color  
     DTL_BKc  =12;                  % Dialog Title
     PUP_BKc  =13;                  % Popup Background
     XY_AXc   =14;                  % X and Y axis, ticks and tick lables
     XY_LBLc  =15;                  % X and Y axis labels
     LBL_FRc  =16;                  % Label Foreground 
     EDT_FRc  =17;                  % Edit Foreground 
     DTL_FRc  =18;                  % Dialog Title Foreground
     PUP_FRc  =19;                  % Popup Foreground
     CURSORc  =20;                  % Cursor and expansion box 
     MARKc    =21;                  % Mark Cross 
     GRIDc    =22;                  % Grid lines
     OVLYLINEc=23;                  % overlay plot line 
NOCLR = 23;                         % number of color definitions

     GRNc = [0,1,0]; 
     REDc = [1,0,0]; 
     YELc = [1,1,0]; 

     CFACc   = 0.9;             % dim for grouping controls 

%  (NOTE: on label text, use only 'VGA' colors to eliminate shadow)
INIT_COLORc =[...
 [0,0,.25098];...               % Figure Background
 [0,0.50196,0.50196];...        % Dialog background
 [0.75294,0.75294,0.75294];...  % label text background
 [1,1,0];...                    % edit text background 
 [0,0,0];...                    % plot axis background
 [0,0,1];...                    % plot label background
 [0,1,0];...                    % plot trace 1 color
 [0,1,1];...                    % plot trace 2 color
 [1,1,0];...                    % plot trace 3 color
 [1,0,0];...                    % plot trace 4 color
 [0.25098,0,0];...              % patch color
 [0,1,0];...                    % dialog title
 [0.75294,0.75294,0.75294];...  % popup background
 [1,1,1];...                    % X and Y axis, ticks and tick lables
 [1,1,1];...                    % X and Y axis labels
 [0,0,0];...                    % label text foreground
 [0,0,0];...                    % edit text foreground
 [0,0,0];...                    % Dialog Title foreground
 [0,0,0];...                    % PopUp text foreground
 [1,1,0];...                    % Cursor
 [1,0,0];...                    % Mark
 [.3,.3,.3];...                 % Grid lines
 [0.5, 1, 0];...                % overlay plot line 
 ]; 

