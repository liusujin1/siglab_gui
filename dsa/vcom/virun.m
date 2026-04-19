  function Out1=virun(Action,In1,In2)

% function Out1=virun(Action,In1,In2)

% A control panel for the VIs


 f1    = 1;
 bvcap = 2;
 bvfg  = 3;
 bvos  = 4;
 bvsa  = 5;
 bvna  = 6;
 bvss  = 7;
 bvid  = 8;
 bcol  = 9;

 lvcap = 10;
 lvfg  = 11;
 lvos  = 12;
 lvsa  = 13;
 lvna  = 14;
 lvss  = 15;
 lvid  = 16;
 lcol  = 17;

 bquit = 18;
 bhelp = 19; 

 vcol_h;   % color definition indices

 BTNw = 45;
 BTNh = 20;
 TXTw = 142;
 TXTh = 18;

 x1 = 5;
 x2 = x1 + BTNw + 5; 

 y1 = 15;        % quit/help
 y2 = y1 + 38;   % color
 y3 = y2 + 40;   % vid
 y4 = y3 + 25;   % vss
 y5 = y4 + 25;   % vna
 y6 = y5 + 40;   % vfg
 y7 = y6 + 25;   % vcap
 y8 = y7 + 25;   % vsa
 y9 = y8 + 25;   % vos

 global HVIRUN_;

 if nargin==0 
     Action='init';
 else   % do nothing if virun hasn't been initialized
     if isempty(findobj('name','virun'))
         return; 
     end
     my_colors=get(HVIRUN_(f1),'userdata');
 end

 if strcmp(Action,'init')
    if exist('vistart.m','file')                       % here if called from matlabrc.m
      if ~beyondv4
          % showfig may not work under v5 
          eval('showfig(''MATLAB Command Window'',''iconify'')');   % minimize command window
      end

      [drv,vpath]=pathfind('vbin');
      eval(['delete ' drv vpath '\vistart.m']); % delete autostart file
    end

      % assume vi_color exists

      VIfont = 0;  % Use default font.

      load vi_color; 

      my_colors=stored_vi_colors;

      if beyondv4
          SaveFont = eval('uifont(VIfont)');
      end

      HVIRUN_(f1)= figure('Units','Pixels','NumberTitle','off',...
                          'position',pos_clip([1500,1000,x2+TXTw+5,y9+25])-[60,40,0,0],...
                          'Color',my_colors(FIG_BKc,:),...
                          'Name','virun','menu','none',...
                          'userdata',my_colors,'Resize','off');

      hf = HVIRUN_(f1);

      if beyondv4
         set(HVIRUN_(f1),'CloseRequestFcn','virun(''quit'')');
      end

      % VOS BUTTON 
      HVIRUN_(lvos) = uicontrol(hf,'Style','Text',...
                                'Position',[x2 y9+1 TXTw TXTh],...
                                'String',' Oscilloscope',...
                                'BackGroundColor',my_colors(LBL_BKc,:),...
                                'HorizontalAlignment','Left');                    

      HVIRUN_(bvos) = uicontrol(hf,'Style','Pushbutton',...
                                'Position',[x1 y9 BTNw BTNh],...
                                'String','vos',...
                                'userdata',0,...
                                'Callback','virun(''run'',''vos'',1)');

      % VSA BUTTON 
      HVIRUN_(lvsa) = uicontrol(hf,'Style','Text',...
                                'Position',[x2 y8+1 TXTw TXTh],...
                                'String',' Spectrum Analyzer',...
                                'BackGroundColor',my_colors(LBL_BKc,:),...
                                'HorizontalAlignment','Left');                    

      HVIRUN_(bvsa) = uicontrol(hf,'Style','Pushbutton',...
                                'Position',[x1 y8 BTNw BTNh],...
                                'String','vsa',...
                                'userdata',0,...
                                'Callback','virun(''run'',''vsa'',1)');

      % VCAP BUTTON 
      HVIRUN_(lvcap) = uicontrol(hf,'Style','Text',...
                                'Position',[x2 y7+1 TXTw TXTh],...
                                'String',' Transient Capture',...
                                'BackGroundColor',my_colors(LBL_BKc,:),...
                                'HorizontalAlignment','Left');                    

      HVIRUN_(bvcap) = uicontrol(hf,'Style','Pushbutton',...
                                'Position',[x1 y7 BTNw BTNh],...
                                'String','vcap',...
                                'userdata',0,...
                                'enable','off',...
                                'Callback','virun(''run'',''vcap'',1)');

      % VFG BUTTON 
      HVIRUN_(lvfg) = uicontrol(hf,'Style','Text',...
                                'Position',[x2 y6+1 TXTw TXTh],...
                                'String',' Function Generator',...
                                'BackGroundColor',my_colors(LBL_BKc,:),...
                                'HorizontalAlignment','Left');                    

      HVIRUN_(bvfg) = uicontrol(hf,'Style','Pushbutton',...
                                'Position',[x1 y6 BTNw BTNh],...
                                'String','vfg',...
                                'userdata',0,...
                                'Callback','virun(''run'',''vfg'',1)');

      % VNA BUTTON 
      HVIRUN_(lvna) = uicontrol(hf,'Style','Text',...
                                'Position',[x2 y5+1 TXTw TXTh],...
                                'String',' Network Analyzer',...
                                'BackGroundColor',my_colors(LBL_BKc,:),...
                                'userdata',0,...
                                'HorizontalAlignment','Left');                    

      HVIRUN_(bvna) = uicontrol(hf,'Style','Pushbutton',...
                                'Position',[x1 y5 BTNw BTNh],...
                                'String','vna',...
                                'userdata',0,...
                                'Callback','virun(''run'',''vna'',1)');

      % VSS BUTTON 
      HVIRUN_(lvss) = uicontrol(hf,'Style','Text',...
                                'Position',[x2 y4+1 TXTw TXTh],...
                                'String',' Swept Sine Analyzer',...
                                'BackGroundColor',my_colors(LBL_BKc,:),...
                                'userdata',0,...
                                'HorizontalAlignment','Left');                    

      HVIRUN_(bvss) = uicontrol(hf,'Style','Pushbutton',...
                                'Position',[x1 y4 BTNw BTNh],...
                                'String','vss',...
                                'userdata',0,...
                                'Callback','virun(''run'',''vss'',1)');

      % VID BUTTON 
      HVIRUN_(lvid) = uicontrol(hf,'Style','Text',...
                                'Position',[x2 y3+1 TXTw TXTh],...
                                'String',' System Identification',...
                                'BackGroundColor',my_colors(LBL_BKc,:),...
                                'HorizontalAlignment','Left');                    

      HVIRUN_(bvid) = uicontrol(hf,'Style','Pushbutton',...
                                'Position',[x1 y3 BTNw BTNh],...
                                'String','vid',...
                                'userdata',0,...
                                'Callback','virun(''run'',''vid'',1)');

      % COLOR BUTTON 
      HVIRUN_(lcol) = uicontrol(hf,'Style','Text',...
                                'Position',[x2 y2+1 TXTw TXTh],...
                                'String',' Color Selection',...
                                'BackGroundColor',my_colors(LBL_BKc,:),...
                                'HorizontalAlignment','Left');                    

      HVIRUN_(bcol) = uicontrol(hf,'Style','Pushbutton',...
                                'Position',[x1 y2 BTNw BTNh],...
                                'String','color',...
                                'userdata',0,...
                                'Callback','virun(''run'',''vicolor'',1)');

      uicontrol(hf,'Style','Frame','Position',[70,y1-6,2*BTNw+30,BTNh+12],...
                   'BackGroundColor',my_colors(DLG_BKc,:));

      % QUIT BUTTON
      HVIRUN_(bquit) = uicontrol(hf,'Style','Pushbutton',...
                            'Position',[135 y1 BTNw BTNh],...
                            'String','Quit',...
                            'Callback','virun(''quit'')');

      % HELP BUTTON
      HVIRUN_(bhelp) = uicontrol(hf,'Style','Pushbutton',...
                            'Position',[80 y1 BTNw BTNh],...
                            'String','Help',...
                            'Callback','vihelp');

      if beyondv4
          eval('uifont(SaveFont);'); 
      end

% end of init
  elseif strcmp(Action,'run')

      if strcmp(In1,'vos')

         if get(HVIRUN_(bvos),'userdata')==0

           set(HVIRUN_(bvos),'userdata',1);

           set(HVIRUN_(lvos),'BackGroundColor',my_colors(EDT_BKc,:));

           set(HVIRUN_(bvcap:bcol),'enable','off');  % disable all 

           if nargin>2 eval('vos');  drawnow; end;

           set(HVIRUN_([bvcap,bvfg,bcol]),'enable','on');

         end

      elseif strcmp(In1,'vsa')

         if get(HVIRUN_(bvsa),'userdata')==0
           set(HVIRUN_(bvsa),'userdata',1);
           set(HVIRUN_(lvsa),'BackGroundColor',my_colors(EDT_BKc,:));
           set(HVIRUN_(bvcap:bcol),'enable','off');  % disable all 
           if nargin>2
               eval('vsa');
               drawnow;
           end
           set(HVIRUN_([bvcap,bvfg,bcol]),'enable','on');
         end

      elseif strcmp(In1,'vcap')

         if get(HVIRUN_(bvcap),'userdata')==0

           set(HVIRUN_(bvcap),'userdata',1,'enable','off');

           set(HVIRUN_(lvcap),'BackGroundColor',my_colors(EDT_BKc,:));

           if beyondv4

              on_off = get(HVIRUN_(bvcap:bcol),'enable'); % current state

              set(HVIRUN_(bvcap:bcol),'enable','off');    % disable all

           end                                           

           

           if nargin>2 eval('vcap'); drawnow; end;

           if beyondv4

              set(HVIRUN_(bcol),'enable','on');

              if strcmp(on_off(bvfg-1),'on')

                 set(HVIRUN_(bvfg),'enable','on');

              end

           end  

         end

      elseif strcmp(In1,'vna') 
         if get(HVIRUN_(bvna),'userdata')==0
           set(HVIRUN_(bvna),'userdata',1);
           set(HVIRUN_(lvna),'BackGroundColor',my_colors(EDT_BKc,:));
           if beyondv4
              on_off = get(HVIRUN_(bvcap:bcol),'enable'); % current state
              set(HVIRUN_(bvcap:bcol),'enable','off');    % disable all
           else
              set(HVIRUN_(bvcap:bvid),'enable','off');
           end

           if nargin>2
               eval('vna');
               drawnow;
           end

           if beyondv4
              set(HVIRUN_(bcol),'enable','on');
              if strcmp(on_off(bvfg-1),'on')
                 set(HVIRUN_(bvfg),'enable','on');
              end
           end
         end

      elseif strcmp(In1,'vss')

          if get(HVIRUN_(bvss),'userdata')==0

           set(HVIRUN_(bvss),'userdata',1);

           set(HVIRUN_(lvss),'BackGroundColor',my_colors(EDT_BKc,:));

           set(HVIRUN_(bvcap:bcol),'enable','off');    % disable all

           if nargin>2 eval('vss'); drawnow; end;

           set(HVIRUN_(bcol),'enable','on');

          end

      elseif strcmp(In1,'vid')

         if get(HVIRUN_(bvid),'userdata')==0
           set(HVIRUN_(bvid),'userdata',1);
           set(HVIRUN_(lvid),'BackGroundColor',my_colors(EDT_BKc,:));
           set(HVIRUN_(bvcap:bcol),'enable','off');    % disable all
           if nargin>2
               eval('vid');
               drawnow;
           end
           set(HVIRUN_(bcol),'enable','on');
         end

      elseif strcmp(In1,'vfg')

          if get(HVIRUN_(bvfg),'userdata')==0

            set(HVIRUN_(bvfg),'userdata',1,'enable','off');

            set(HVIRUN_(lvfg),'BackGroundColor',my_colors(EDT_BKc,:));

            if beyondv4

               on_off = get(HVIRUN_(bvcap:bcol),'enable'); % current state

               set(HVIRUN_(bvcap:bcol),'enable','off');    % disable all

            else

               set(HVIRUN_(bvna:bvid),'enable','off');

            end;   

            if nargin>2 eval('vfg'); drawnow; end;

            if beyondv4

               % can run vos,vsa or vcolor 

               set(HVIRUN_(bcol),'enable','on');

               if strcmp(on_off(bvsa-1),'on')

                 set(HVIRUN_(bvsa),'enable','on');

               end;

               if strcmp(on_off(bvos-1),'on')

                 set(HVIRUN_(bvos),'enable','on');

               end;

            end;

          end;  

      

      elseif strcmp(In1,'vicolor')

          if get(HVIRUN_(bcol),'userdata')==0

             set(HVIRUN_(lcol),'BackGroundColor',my_colors(EDT_BKc,:));

             set(HVIRUN_(bcol),'userdata',1,'enable','off');

             

             if beyondv4

               on_off = get(HVIRUN_(bvcap:bcol),'enable'); % current state

               set(HVIRUN_(bvcap:bcol),'enable','off');    % disable all

             end;

             

             if nargin>2 eval('vicolor'); drawnow; end;

             

             if beyondv4

                for i= 1:length(on_off)

                   if strcmp(on_off(i),'on')

                      set(HVIRUN_(i+1),'enable','on');

                   end;

                end;

             end;

          end;

      else

          disp('unrecognized command in virun')

      end;



  elseif strcmp(Action,'close') 

      if strcmp(In1,'vos')

         if get(HVIRUN_(bvos),'userdata')==1

           set(HVIRUN_(bvos),'userdata',0);

           set(HVIRUN_(lvos),'BackGroundColor',my_colors(LBL_BKc,:));

           set(HVIRUN_([bvos bvsa]),'enable','on');

           set(HVIRUN_(bvcap),'enable','off');

           if get(HVIRUN_(bvfg),'userdata')==0

             set(HVIRUN_(bvna:bvid),'enable','on');

           end;

         end; 

      elseif strcmp(In1,'vsa') 

         if get(HVIRUN_(bvsa),'userdata')==1

           set(HVIRUN_(bvsa),'userdata',0);

           set(HVIRUN_(lvsa),'BackGroundColor',my_colors(LBL_BKc,:));

           set(HVIRUN_([bvos bvsa]),'enable','on');

           set(HVIRUN_(bvcap),'enable','off');

           if get(HVIRUN_(bvfg),'userdata')==0

             set(HVIRUN_(bvna:bvid),'enable','on');

           end;

         end;  

      elseif strcmp(In1,'vcap') 

         if get(HVIRUN_(bvcap),'userdata')==1

           set(HVIRUN_(bvcap),'userdata',0,'enable','on');

           set(HVIRUN_(lvcap),'BackGroundColor',my_colors(LBL_BKc,:));

         end;  

     

      elseif strcmp(In1,'vna')

         if get(HVIRUN_(bvna),'userdata')==1

           set(HVIRUN_(bvna),'userdata',0);

           set(HVIRUN_(lvna),'BackGroundColor',my_colors(LBL_BKc,:));

           set(HVIRUN_(bvfg:bvid),'enable','on');

         end;  

     

      elseif strcmp(In1,'vss')
         if get(HVIRUN_(bvss),'userdata')==1

           set(HVIRUN_(bvss),'userdata',0);

           set(HVIRUN_(lvss),'BackGroundColor',my_colors(LBL_BKc,:));

           set(HVIRUN_(bvfg:bvid),'enable','on');

         end 

      elseif strcmp(In1,'vid')

         if get(HVIRUN_(bvid),'userdata')==1

           set(HVIRUN_(bvid),'userdata',0);

           set(HVIRUN_(lvid),'BackGroundColor',my_colors(LBL_BKc,:));

           set(HVIRUN_(bvfg:bvid),'enable','on');

         end

      elseif strcmp(In1,'vfg')

         if get(HVIRUN_(bvfg),'userdata')==1
            set(HVIRUN_(bvfg),'userdata',0,'enable','on');
            set(HVIRUN_(lvfg),'BackGroundColor',my_colors(LBL_BKc,:));
            if get(HVIRUN_(bvos),'userdata')==0 && get(HVIRUN_(bvsa),'userdata')==0
              set(HVIRUN_(bvna:bvid),'enable','on');
            end
         end

      elseif strcmp(In1,'vicolor')
          if get(HVIRUN_(bcol),'userdata')==1
             set(HVIRUN_(lcol),'BackGroundColor',my_colors(LBL_BKc,:));
             set(HVIRUN_(bcol),'userdata',0,'enable','on');
          end
      else

      end

  elseif strcmp(Action,'quit')
    if ~beyondv4
       eval('showfig(''MATLAB Command Window''); shc;'); % show command window
    end

    delete(HVIRUN_(f1));
    clear HVIRUN_;
 end  % end if strcmp(Action)

% end function virun




















